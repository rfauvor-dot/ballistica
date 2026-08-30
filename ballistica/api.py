"""HTTP API wrapping the ballistics engine, so the phone app (field
use, voice) and web app (data entry/review) can share one backend
instead of each reimplementing the physics.

Every data-touching endpoint is per-user and auth-gated (Supabase Auth
+ Postgres RLS -- see MULTI_TENANCY_DESIGN.md and supabase_store.py).
There used to be a second, unauthenticated single-tenant surface here
(a single shared ProfileStore/BallisticaCLI, no login) kept alive
during the cutover as a rollback safety net -- removed 2026-08-28 once
the multi-tenant path was confirmed stable in production and two
independent reviews flagged it as exposed surface doing nothing useful
rather than an actual safety net. The standalone single-tenant CLI
(`python -m ballistica.cli`) is untouched and still works exactly as
before -- this only removes the HTTP surface, not the underlying
engine or the CLI itself.

Run with: uvicorn ballistica.api:app --reload
Interactive docs at http://127.0.0.1:8000/docs once it's running.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Loads .env (gitignored, holds OPENAI_API_KEY) into the process
# environment before anything below reads it -- must run before the
# OpenAI import/client construction, not after.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import httpx
import jwt
import openai
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from .atmosphere import AtmosphereConditions, pressure_at_altitude_inhg
from .cli import BallisticaCLI, _CalibrationSession, _SetupSession
from .openai_client import get_openai_client
from .profiles import Load, Rifle
from .reporting import report_for_point
from .supabase_auth import verify_token
from .supabase_store import SupabaseProfileStore
from .trajectory import TrajectorySolver, WindCondition
from .waiver import (
    WAIVER_ACKNOWLEDGMENT_TEXT, WAIVER_SECTIONS, WAIVER_TEXT_SHA256, WAIVER_TITLE, WAIVER_VERSION,
)
from .weather import fetch_nearest_station_conditions

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
# Only ever used by v2_delete_account below, for exactly one narrow
# purpose: actually removing a user's own login (auth.users row) once
# their own token has already been independently verified. Every other
# endpoint in this file uses the caller's own access token, never this
# key -- see supabase_store.py's docstring for why that's the real
# isolation boundary. This is the one deliberate, narrow exception,
# not a precedent for using it more broadly.
_SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

app = FastAPI(title="Ballistica API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten once the web/phone clients have real origins
    allow_methods=["*"],
    allow_headers=["*"],
)


def _ip_rate_limit_key(request: Request) -> str:
    """IP-based fallback key. Render (like most PaaS) terminates TLS
    and proxies every request through its own load balancer, so
    request.client.host would be Render's proxy address on every
    request -- not the real caller -- unless the ASGI server is started
    with proxy-header trust, which isn't something this code can verify
    from here (the start command lives in Render's dashboard, not this
    repo). Reading X-Forwarded-For directly (its first, left-most entry
    -- the original client, per the standard convention for a single
    trusted proxy hop) is the more reliable signal on a platform shaped
    like this. Falls back to the raw connection address for local/dev
    use, where nothing is proxying requests at all. Trusting this header
    assumes requests only ever reach the app through Render's own proxy
    -- true today, but would become spoofable if the app were ever
    exposed directly on another host without a trusted proxy in front."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return "ip:" + forwarded.split(",")[0].strip()
    return "ip:" + get_remote_address(request)


def _rate_limit_key(request: Request) -> str:
    """Per-user where possible, per-IP otherwise (2026-08-29, per Rick's
    instruction: people sharing a connection -- same office/home
    network, same NAT -- shouldn't throttle each other). slowapi's
    key_func runs as ASGI middleware, before the route's own auth
    dependency executes, so there's no already-verified user id sitting
    on the request to reuse here -- this independently re-verifies the
    bearer token itself. That's a real, deliberate signature check, not
    a cheap unverified decode: using an unverified `sub` would let an
    attacker manufacture a fresh, never-throttled bucket per request
    just by changing a claim in a token with no valid signature, which
    would quietly defeat rate limiting for exactly the requests it most
    needs to catch. A token that fails verification here falls straight
    through to the same IP-based key as before -- no regression for the
    unauthenticated-abuse case this was already protecting against.
    The extra verification is cheap (JWKS keys are cached by PyJWKClient
    after the first fetch) and duplicates work the route's own auth
    dependency does moments later anyway."""
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        try:
            return "user:" + verify_token(auth_header[len("Bearer "):])
        except Exception:
            pass  # falls through to the IP-based key below
    return _ip_rate_limit_key(request)


# Standard approach (per-IP, in-memory -- Render runs this as a single
# instance, so no shared/Redis-backed store is needed at this scale):
# a generous blanket default covers every route automatically via
# SlowAPIMiddleware below, and the three endpoints that proxy paid,
# per-call third-party APIs (OpenAI Whisper/TTS, and /v2/voice/query's
# Claude-backed intent fallback) get a tighter limit of their own --
# those are the real cost-abuse and runaway-client-loop exposure, not
# the cheap CRUD endpoints. Numbers are a starting point, not a
# measured ceiling -- sanity-check against real usage patterns
# (multi-turn setup/calibration can fire several requests a minute
# legitimately) and adjust if they turn out too tight or too loose.
limiter = Limiter(key_func=_rate_limit_key, default_limits=["100/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

_WEB_DIR = Path(__file__).resolve().parent / "web"
_WEB_INDEX = _WEB_DIR / "index.html"

app.mount("/icons", StaticFiles(directory=_WEB_DIR / "icons"), name="icons")
# The four generated walkthrough narration MP3s -- see
# ballistica/web/audio/README.md. Static, unauthenticated (same as
# /icons): fixed, non-personalized narration content, nothing per-user
# about the files themselves -- the per-user state is only ever "has
# this account heard section 1 yet," tracked separately via
# /v2/walkthrough-status, not by gating access to the audio files.
app.mount("/audio", StaticFiles(directory=_WEB_DIR / "audio"), name="audio")
# Static marketing/branding assets (the corner thumbnail, etc.) -- same
# unauthenticated, non-personalized static-file pattern as /icons and /audio.
app.mount("/images", StaticFiles(directory=_WEB_DIR / "images"), name="images")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def web_ui():
    # index.html is read (not served as a static file) specifically so
    # the public Supabase config can be injected per-request -- the
    # anon key is meant to be public/client-side by design (RLS is the
    # real security boundary, not keeping this secret), but it still
    # has to come from server-side env vars rather than being
    # hardcoded into the committed HTML.
    html = _WEB_INDEX.read_text(encoding="utf-8")
    html = html.replace("__SUPABASE_URL__", os.environ.get("SUPABASE_URL", ""))
    html = html.replace("__SUPABASE_ANON_KEY__", os.environ.get("SUPABASE_ANON_KEY", ""))
    # Root-caused live (2026-08-28): this response had no cache-control
    # headers at all, so a browser's own caching heuristics -- or,
    # worse, back-forward-cache serving a fully-instantiated stale page
    # with no network request whatsoever -- could keep running whatever
    # JS was loaded before a deploy indefinitely, even after a hard
    # reload in some cases. Confirmed as the cause of a real report: a
    # signup that appeared to skip the waiver screen entirely and go
    # straight to a Supabase auth error, which only happens on JS from
    # before the waiver flow existed. This page is dynamically
    # generated per request (live config injection) and drives a legal-
    # acceptance flow -- it must never be served stale, so every
    # response explicitly forbids caching rather than relying on
    # whatever a given browser's default heuristics decide to do.
    return HTMLResponse(
        content=html,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


@app.get("/manifest.json", include_in_schema=False)
def web_manifest():
    return FileResponse(_WEB_DIR / "manifest.json", media_type="application/manifest+json")


# Served from the root path (not under /icons or another mount) so its
# default scope covers the whole origin -- a service worker only
# controls requests under the path it's served from. See sw.js's own
# header comment for why it's safe alongside the no-cache "/" route.
@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(_WEB_DIR / "sw.js", media_type="application/javascript")


# Offline-mode ballistic engine (see MULTI_TENANCY_DESIGN.md §19) --
# plain JS, no per-request templating, safe to cache normally.
@app.get("/engine.js", include_in_schema=False)
def offline_engine_script():
    return FileResponse(_WEB_DIR / "engine.js", media_type="application/javascript")


# ---------------------------------------------------------------- schemas

class AtmosphereIn(BaseModel):
    temp_f: float = 59.0
    pressure_inhg: float | None = Field(
        None, description="Station pressure if known. Omit/null to estimate from "
                           "altitude_ft instead -- entering altitude alone does "
                           "nothing unless pressure is also either provided or left "
                           "unset for this estimate.",
    )
    humidity_pct: float = 0.0
    altitude_ft: float = 0.0

    def to_conditions(self) -> AtmosphereConditions:
        pressure = self.pressure_inhg
        if pressure is None:
            # No real barometer reading -- estimate from altitude via the
            # standard barometric formula rather than silently defaulting
            # to sea-level pressure regardless of what altitude was given.
            pressure = pressure_at_altitude_inhg(self.altitude_ft)
        return AtmosphereConditions(
            temp_f=self.temp_f, pressure_inhg=pressure,
            humidity_pct=self.humidity_pct, altitude_ft=self.altitude_ft,
        )


class WindIn(BaseModel):
    speed_mph: float = 0.0
    clock_deg: float = Field(
        0.0, description="0 = 12 o'clock headwind, 90 = 3 o'clock (pushes bullet left), "
                          "180 = tailwind, 270 = 9 o'clock.",
    )

    def to_condition(self) -> WindCondition:
        return WindCondition(speed_mph=self.speed_mph, clock_deg=self.clock_deg)


class LoadIn(BaseModel):
    name: str
    bullet_weight_gr: float
    bc: float
    drag_model: str = Field(description="'G1' or 'G7'")
    muzzle_velocity_fps: float
    zero_distance_yd: float
    bullet_type: str = ""
    powder: str = ""
    powder_charge_gr: float | None = None
    notes: str = ""


class LoadOut(LoadIn):
    pass


class RifleIn(BaseModel):
    name: str
    scope_height_in: float
    caliber: str = ""
    barrel_length_in: float | None = None
    twist_rate: str = ""
    click_value_mrad: float = 0.1
    reticle_unit: str = Field("MRAD", description="'MRAD' or 'MOA' -- which unit the optic's turrets use")
    optic_type: str = Field("", description="'scope' (magnified), 'red_dot' (fixed 1x reflex/holographic), "
                                             "or '' if unknown -- determines which of the fields below "
                                             "actually apply (a red dot has no magnification or focal plane)")
    scope_make: str = ""
    scope_model: str = ""
    magnification: str = ""
    objective_lens_mm: float | None = None
    focal_plane: str = Field("", description="'FFP', 'SFP', or '' if unknown/not applicable -- magnified "
                                              "scopes only, not used by today's turret-dial calculations, "
                                              "but will matter once a reticle-holdover or rangefinder "
                                              "feature exists")
    reticle_type: str = ""
    dot_size_moa: float | None = Field(None, description="Red dot only -- the dot's apparent size in MOA")
    has_suppressor: bool = False
    suppressor_type: str = Field("", description="Open text -- a real brand if there is one, or a "
                                                   "generic/custom description if not. Tied to the "
                                                   "rifle, not any one load.")
    loads: list[LoadIn] = []


class RifleUpdate(BaseModel):
    """Full replace of a rifle's editable metadata (not its loads)."""
    scope_height_in: float
    caliber: str = ""
    barrel_length_in: float | None = None
    twist_rate: str = ""
    click_value_mrad: float = 0.1
    reticle_unit: str = "MRAD"
    optic_type: str = ""
    scope_make: str = ""
    scope_model: str = ""
    magnification: str = ""
    objective_lens_mm: float | None = None
    focal_plane: str = ""
    reticle_type: str = ""
    dot_size_moa: float | None = None
    has_suppressor: bool = False
    suppressor_type: str = ""


class RifleSummary(BaseModel):
    name: str
    active_load_name: str | None
    load_count: int


class RifleDetail(BaseModel):
    name: str
    scope_height_in: float
    caliber: str
    barrel_length_in: float | None
    twist_rate: str
    click_value_mrad: float
    reticle_unit: str
    optic_type: str
    scope_make: str
    scope_model: str
    magnification: str
    objective_lens_mm: float | None
    focal_plane: str
    reticle_type: str
    dot_size_moa: float | None
    has_suppressor: bool
    suppressor_type: str
    active_load_name: str | None
    loads: list[LoadOut]


class CalcRequest(BaseModel):
    rifle: str | None = Field(None, description="Fuzzy match; omit to use the active rifle")
    load: str | None = Field(None, description="Fuzzy match; omit to use the rifle's active load")
    atmosphere: AtmosphereIn = AtmosphereIn()
    wind: WindIn = WindIn()


class DropAtRangeRequest(CalcRequest):
    range_yd: float


class RangeReportOut(BaseModel):
    range_yd: float
    drop_in: float
    drop_moa: float
    drop_mrad: float
    drop_clicks: float
    windage_in: float
    windage_moa: float
    windage_mrad: float
    windage_clicks: float
    velocity_fps: float
    mach: float
    time_s: float


class VoiceQueryIn(BaseModel):
    text: str = Field(description="Transcribed speech, e.g. \"what's my drop at 500 yards\"")


class VoiceQueryOut(BaseModel):
    reply: str = Field(description="Text meant to be spoken back via TTS -- terse and numeric by design")
    awaiting_response: bool = Field(
        False,
        description="True mid-conversation (guided load/rifle setup, chronograph calibration): the "
                     "frontend should keep listening for the next answer without requiring the wake "
                     "word again.",
    )


class VoiceSpeakIn(BaseModel):
    text: str
    voice: str = Field(
        "shimmer", description="OpenAI TTS voice name. Rick's pick, chosen by ear against real "
                                "samples of all 9 stock voices on the actual reply phrasing -- "
                                "not a guess, don't second-guess it without asking first.",
    )
    speed: float = Field(
        0.9, ge=0.25, le=4.0,
        description="OpenAI TTS speed multiplier. Default slowed from the API's own 1.0 default "
                    "after live feedback that full-speed replies were hard to follow at the range.",
    )


class WaiverAcceptIn(BaseModel):
    waiver_version: str
    waiver_sha256: str
    accepted_at: str = Field(description="ISO 8601 timestamp captured client-side the moment the "
                                          "checkbox was checked and submitted -- not the time this "
                                          "request happens to arrive.")


class ProfileUpdateIn(BaseModel):
    # Mirrors db/008_display_name.sql's own check constraint -- validated
    # here too so a bad value gets a clean 422 instead of a raw Postgres
    # constraint-violation error surfacing to the client.
    display_name: str = Field(min_length=1, max_length=40)


# ------------------------------------------------------------------ helpers

def _msg(exc: Exception) -> str:
    """KeyError.__str__ wraps its message in an extra repr() layer (so
    str(KeyError("no rifle")) == '"no rifle"', quotes and all) -- this
    unwraps that so API error details read as plain text either way."""
    return exc.args[0] if exc.args else str(exc)


def _load_to_out(load: Load) -> LoadOut:
    return LoadOut(**load.__dict__)


def _rifle_to_detail(rifle: Rifle) -> RifleDetail:
    return RifleDetail(
        name=rifle.name, scope_height_in=rifle.scope_height_in, caliber=rifle.caliber,
        barrel_length_in=rifle.barrel_length_in, twist_rate=rifle.twist_rate,
        click_value_mrad=rifle.click_value_mrad, reticle_unit=rifle.reticle_unit,
        optic_type=rifle.optic_type, scope_make=rifle.scope_make, scope_model=rifle.scope_model,
        magnification=rifle.magnification, objective_lens_mm=rifle.objective_lens_mm,
        focal_plane=rifle.focal_plane, reticle_type=rifle.reticle_type,
        dot_size_moa=rifle.dot_size_moa, has_suppressor=rifle.has_suppressor,
        suppressor_type=rifle.suppressor_type, active_load_name=rifle.active_load_name,
        loads=[_load_to_out(load) for load in rifle.loads.values()],
    )


# ----------------------------------------------------------------- routes

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/waiver")
def get_waiver(response: Response) -> dict:
    """Public, no auth -- has to be readable before an account exists at
    all (the whole point of the account-creation waiver screen). Serves
    the canonical text from waiver.py as structured JSON so index.html
    renders it directly rather than keeping its own separate copy that
    could drift from what actually gets hashed. version/sha256 are what
    the frontend echoes back on POST /v2/waiver/accept -- see that
    endpoint for why both matter, not just one.

    Explicit no-cache headers for the same reason as "/" above: this
    drives a legal-acceptance flow, and a browser serving a stale
    cached copy after Rick revises the waiver text would let someone
    accept an outdated version without ever seeing the current one --
    flagged by an external security review (2026-08-29), same
    staleness class as the bug already fixed for the page itself."""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return {
        "version": WAIVER_VERSION,
        "sha256": WAIVER_TEXT_SHA256,
        "title": WAIVER_TITLE,
        "sections": [
            {"heading": s.heading, "paragraphs": list(s.paragraphs), "bullets": list(s.bullets)}
            for s in WAIVER_SECTIONS
        ],
        "acknowledgment_text": WAIVER_ACKNOWLEDGMENT_TEXT,
    }


@app.post("/voice/speak")
@limiter.limit("20/minute")
def voice_speak(request: Request, payload: VoiceSpeakIn):
    """Text in, MP3 bytes out via OpenAI TTS. The other half of the
    voice loop from /voice/query -- feed that endpoint's reply straight
    into this one to get spoken audio back. Tighter rate limit than the
    blanket default: this proxies a paid, per-call OpenAI API and has no
    auth gate at all (stateless, no per-user data -- see the module
    docstring), so IP-based limiting is the only cost-abuse control it has."""
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text must not be empty")
    try:
        client = get_openai_client()
        result = client.audio.speech.create(
            model="tts-1", voice=payload.voice, input=text, speed=payload.speed,
        )
    except openai.OpenAIError as exc:
        raise HTTPException(status_code=502, detail=f"TTS request failed: {exc}")
    return Response(content=result.content, media_type="audio/mpeg")


_TRANSCRIBE_MODEL = "gpt-4o-transcribe"
# Biases the model toward Ballistica's actual vocabulary -- verified live
# (Addendum 15) that without this, terms like calibers and powder/BC
# figures are exactly the kind of short, jargon-heavy phrase a general-
# purpose transcription pass is most likely to mangle.
_TRANSCRIBE_PROMPT = (
    "Ballistics and rifle terminology: MOA, MRAD, elevation, windage, "
    "zero, drag coefficient, chronograph, calibers like 5.7x28mm or "
    "6.5 Creedmoor, powder charge, muzzle velocity, H335 powder, "
    "Sierra MatchKing."
)


@app.post("/voice/transcribe")
@limiter.limit("20/minute")
async def voice_transcribe(request: Request, audio: UploadFile = File(...)):
    """Recorded audio in (whatever format the browser's MediaRecorder
    produced -- webm/ogg/mp4 are all fine, OpenAI's transcription
    endpoint handles the common ones), transcribed text out. First
    third of the full voice loop: this -> /voice/query -> /voice/speak.
    Same tighter limit and same reasoning as /voice/speak above -- a
    paid per-call API, no auth gate."""
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="audio file was empty")
    try:
        client = get_openai_client()
        # OpenAI's SDK identifies the audio format from the filename's
        # extension, not the content-type header, so this needs a real
        # name attached, not just raw bytes.
        file_tuple = (audio.filename or "recording.webm", audio_bytes, audio.content_type or "audio/webm")
        result = client.audio.transcriptions.create(
            model=_TRANSCRIBE_MODEL, file=file_tuple, language="en", prompt=_TRANSCRIBE_PROMPT,
        )
    except openai.OpenAIError as exc:
        raise HTTPException(status_code=502, detail=f"Transcription failed: {exc}")
    return {"text": result.text}


# ------------------------------------------------------------ multi-tenant
# Every data-touching endpoint below is auth-gated (MULTI_TENANCY_DESIGN.md)
# -- this is the app's only data surface as of the 2026-08-28 single-tenant
# removal, not a parallel path anymore. `/calc/drop-table`, `/calc/mpbr-
# zero`, and `/calc/angle` had no /v2 equivalent when the single-tenant
# versions were removed -- nothing (the web UI included) called them
# directly, and the same capability stays reachable through /v2/voice/query
# ("table to 800 yards", "what zero minimizes...", "I'm seeing 3 clicks...",
# all handled by BallisticaCLI.handle() same as any other voice command) --
# so removing them was a real reduction in exposed surface, not a lost
# capability. A REST-shaped (non-voice) /v2 version of any of the three is
# a small, separate follow-up if Rick ever wants one, not done here.

def _verify_bearer(authorization: str = Header(...)) -> tuple[str, str]:
    """FastAPI dependency: verifies the bearer token and returns
    (user_id, access_token). The common seam every auth-gated endpoint
    ultimately depends on -- _get_user_store below wraps this for
    endpoints that need the full per-user rifle/load store; endpoints
    that only need to know who's asking (e.g. recording waiver
    acceptance) can depend on this directly instead of paying for a
    SupabaseProfileStore's own rifles/loads/conversation_state fetch on
    construction just to throw it away unused."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    token = authorization[len("Bearer "):]
    try:
        user_id = verify_token(token)
    except (jwt.InvalidTokenError, jwt.PyJWKClientError) as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")
    return user_id, token


def _get_user_store(auth: tuple[str, str] = Depends(_verify_bearer)) -> SupabaseProfileStore:
    """FastAPI dependency: returns a per-request store scoped to the
    caller's own access token -- every /v2 rifle/load/voice endpoint's
    data access goes through this, so isolation is enforced by Postgres
    RLS (see supabase_store.py's docstring), not just by this function
    filtering correctly."""
    user_id, token = auth
    return SupabaseProfileStore(user_id, token)


@app.post("/v2/waiver/accept")
def v2_accept_waiver(payload: WaiverAcceptIn, auth: tuple[str, str] = Depends(_verify_bearer)):
    """Records that the authenticated user accepted a specific, exact
    version of the liability waiver -- append-only (db/004: no UPDATE or
    DELETE policy exists on waiver_acceptances at all, for anyone,
    including the row's own owner). Rejects a stale version/hash outright
    rather than silently recording an acceptance of text that isn't the
    one currently live -- if the waiver has been revised since the
    frontend last fetched GET /waiver, that mismatch must surface as an
    error, not get quietly recorded as if it were current.

    This is the durable, structured, queryable half of where an
    acceptance gets recorded. It is deliberately NOT the only place: the
    frontend also passes the same version/hash/timestamp as `data` on
    the Supabase signup call itself (auth.users.raw_user_meta_data),
    captured atomically with account creation regardless of whether
    email confirmation is required -- this endpoint can only be called
    once a session exists, which for a confirmation-required signup is
    not until the user actually confirms and signs in. Belt and
    suspenders: the metadata capture never depends on the user ever
    coming back; this table is the clean, append-only, directly
    queryable record once they do."""
    if payload.waiver_version != WAIVER_VERSION or payload.waiver_sha256 != WAIVER_TEXT_SHA256:
        raise HTTPException(
            status_code=409,
            detail="The waiver has changed since this was loaded -- reload and accept the current version.",
        )
    user_id, token = auth
    resp = httpx.post(
        f"{_SUPABASE_URL}/rest/v1/waiver_acceptances",
        headers={
            "apikey": _SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "user_id": user_id, "waiver_version": payload.waiver_version,
            "waiver_sha256": payload.waiver_sha256, "accepted_at": payload.accepted_at,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return {"recorded": True}


@app.get("/v2/walkthrough-status")
def v2_walkthrough_status(auth: tuple[str, str] = Depends(_verify_bearer)) -> dict:
    """Whether this account has ever been auto-played the first-login
    walkthrough (db/006's first_walkthrough_played_at on `profiles`).
    Ensures a profiles row exists first (upsert, `Prefer:
    resolution=merge-duplicates`, sending only user_id) -- nothing else
    creates one today, and there's no signup trigger for it. Sending
    only user_id in the upsert body is what makes this safe to call
    repeatedly: PostgREST's merge-duplicates only touches columns
    actually present in the request, so an existing row's
    first_walkthrough_played_at is never overwritten by this call."""
    user_id, token = auth
    headers = {
        "apikey": _SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = httpx.post(
        f"{_SUPABASE_URL}/rest/v1/profiles", headers={**headers, "Prefer": "resolution=merge-duplicates,return=representation"},
        json={"user_id": user_id}, params={"on_conflict": "user_id"}, timeout=15,
    )
    resp.raise_for_status()
    rows = resp.json()
    return {"first_walkthrough_played_at": rows[0]["first_walkthrough_played_at"] if rows else None}


@app.post("/v2/walkthrough/mark-first-played")
def v2_mark_walkthrough_first_played(auth: tuple[str, str] = Depends(_verify_bearer)) -> dict:
    """Marks the first-login walkthrough as played for this account --
    idempotent by construction: the PATCH only matches a row where
    first_walkthrough_played_at is still null, so a second call (a
    double-fire from a fast refresh, a retry, whatever) simply matches
    zero rows and does nothing rather than overwriting the real first
    timestamp with a later one. Call GET /v2/walkthrough-status at least
    once first so the profiles row actually exists -- this endpoint
    doesn't create one."""
    user_id, token = auth
    headers = {
        "apikey": _SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    now = datetime.now(timezone.utc).isoformat()
    resp = httpx.patch(
        f"{_SUPABASE_URL}/rest/v1/profiles", headers=headers,
        params={"user_id": f"eq.{user_id}", "first_walkthrough_played_at": "is.null"},
        json={"first_walkthrough_played_at": now}, timeout=15,
    )
    resp.raise_for_status()
    return {"marked": True}


@app.get("/v2/profile")
def v2_get_profile(auth: tuple[str, str] = Depends(_verify_bearer)) -> dict:
    """The display name a user has chosen for themselves, if any --
    used to address them by name in the voice greeting instead of a
    hardcoded name from the single-tenant era (real issue for any real
    account that isn't Rick's -- MULTI_TENANCY_DESIGN.md §23). Same
    ensure-the-row-exists upsert pattern as /v2/walkthrough-status
    above; unset (null) is a normal, expected state, not an error --
    the frontend falls back to name-less greeting phrasing."""
    user_id, token = auth
    headers = {
        "apikey": _SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = httpx.post(
        f"{_SUPABASE_URL}/rest/v1/profiles", headers={**headers, "Prefer": "resolution=merge-duplicates,return=representation"},
        json={"user_id": user_id}, params={"on_conflict": "user_id"}, timeout=15,
    )
    resp.raise_for_status()
    rows = resp.json()
    return {"display_name": rows[0]["display_name"] if rows else None}


@app.patch("/v2/profile")
def v2_update_profile(payload: ProfileUpdateIn, auth: tuple[str, str] = Depends(_verify_bearer)) -> dict:
    user_id, token = auth
    headers = {
        "apikey": _SUPABASE_ANON_KEY, "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    resp = httpx.post(
        f"{_SUPABASE_URL}/rest/v1/profiles",
        headers={**headers, "Prefer": "resolution=merge-duplicates,return=representation"},
        json={"user_id": user_id, "display_name": payload.display_name},
        params={"on_conflict": "user_id"}, timeout=15,
    )
    resp.raise_for_status()
    rows = resp.json()
    return {"display_name": rows[0]["display_name"] if rows else None}


@app.delete("/v2/account")
@limiter.limit("5/minute")
def v2_delete_account(request: Request, auth: tuple[str, str] = Depends(_verify_bearer)):
    """Self-service, permanent account deletion -- confirmed live (Rick,
    2026-08-29): needed for his own testing (no way to reset a test
    account and retry signup), and a real gap for any real user too.

    The user_id being deleted is ALWAYS the one this endpoint's own auth
    dependency just verified from the caller's own token -- never a
    value read from the request body or query string. That's the one
    thing standing between "a user can delete their own account" and "a
    user can delete anyone's account"; there is deliberately no code
    path here that accepts an id from the client at all.

    Deleting the auth.users row (the login itself) requires Supabase's
    admin API, which requires the service_role key -- the one place in
    this codebase that key is used, for exactly this one operation.
    Everywhere else (every other endpoint in this file) uses the
    caller's own access token, per supabase_store.py's whole design.
    Deliberately NOT manually deleting rifles/loads/conversation_state/
    profile/waiver_acceptances first -- every one of those tables' FK to
    auth.users is ON DELETE CASCADE (waiver_acceptances aside, which is
    also CASCADE -- see db/004's own comment on that specific choice),
    so a single admin-level delete of the auth user atomically removes
    all of it in one transaction, exactly matching
    MULTI_TENANCY_DESIGN.md #6.2's original design intent. `events` has
    no user_id column at all anymore (db/005) -- nothing there to touch
    either way."""
    if not _SUPABASE_SERVICE_ROLE_KEY:
        raise HTTPException(
            status_code=503,
            detail="Account deletion isn't configured yet on this server "
                   "(SUPABASE_SERVICE_ROLE_KEY is not set).",
        )
    user_id, _ = auth
    resp = httpx.delete(
        f"{_SUPABASE_URL}/auth/v1/admin/users/{user_id}",
        headers={
            "apikey": _SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {_SUPABASE_SERVICE_ROLE_KEY}",
        },
        timeout=15,
    )
    if resp.status_code not in (200, 204):
        raise HTTPException(status_code=502, detail=f"Account deletion failed: {resp.text}")
    return {"deleted": True}


@app.get("/v2/rifles", response_model=list[RifleSummary])
def v2_list_rifles(user_store: SupabaseProfileStore = Depends(_get_user_store)):
    return [
        RifleSummary(name=r.name, active_load_name=r.active_load_name, load_count=len(r.loads))
        for r in user_store.rifles.values()
    ]


@app.post("/v2/rifles", response_model=RifleDetail)
def v2_create_rifle(payload: RifleIn, user_store: SupabaseProfileStore = Depends(_get_user_store)):
    if payload.name in user_store.rifles:
        raise HTTPException(status_code=409, detail=f"Rifle '{payload.name}' already exists")
    try:
        rifle = Rifle(
            name=payload.name, scope_height_in=payload.scope_height_in,
            caliber=payload.caliber, barrel_length_in=payload.barrel_length_in,
            twist_rate=payload.twist_rate, click_value_mrad=payload.click_value_mrad,
            reticle_unit=payload.reticle_unit, optic_type=payload.optic_type,
            scope_make=payload.scope_make, scope_model=payload.scope_model,
            magnification=payload.magnification, objective_lens_mm=payload.objective_lens_mm,
            focal_plane=payload.focal_plane, reticle_type=payload.reticle_type,
            dot_size_moa=payload.dot_size_moa, has_suppressor=payload.has_suppressor,
            suppressor_type=payload.suppressor_type,
        )
        for i, load_in in enumerate(payload.loads):
            rifle.add_load(Load(**load_in.model_dump()), make_active=(i == 0))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_msg(exc))
    user_store.add_rifle(rifle)
    user_store.save()
    return _rifle_to_detail(rifle)


@app.get("/v2/rifles/{rifle_name}", response_model=RifleDetail)
def v2_get_rifle(rifle_name: str, user_store: SupabaseProfileStore = Depends(_get_user_store)):
    try:
        rifle = user_store.find_rifle(rifle_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_msg(exc))
    return _rifle_to_detail(rifle)


@app.delete("/v2/rifles/{rifle_name}")
def v2_delete_rifle(rifle_name: str, user_store: SupabaseProfileStore = Depends(_get_user_store)):
    try:
        rifle = user_store.delete_rifle(rifle_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_msg(exc))
    user_store.save()
    return {"deleted": rifle.name}


def _hydrate_cli(cli: BallisticaCLI, state: dict) -> None:
    """Reconstructs a BallisticaCLI's in-progress voice-conversation
    state (setup/calibration/pending_delete) from what the previous
    request persisted -- the API is stateless per-request, so this
    stands in for the single-tenant CLI's long-lived in-memory object."""
    cli._setup = _SetupSession.from_dict(state["setup"]) if state.get("setup") else None
    cli._calibration = (
        _CalibrationSession.from_dict(state["calibration"]) if state.get("calibration") else None
    )
    pending_delete = state.get("pending_delete")
    cli._pending_delete = pending_delete["rifle_name"] if pending_delete else None
    cli._pending_delete_at = pending_delete["at"] if pending_delete else 0.0


def _dehydrate_cli(cli: BallisticaCLI) -> dict:
    """The inverse of _hydrate_cli -- what to persist back after
    handle() runs, whatever it left the session state as (unchanged,
    advanced a turn, or cleared entirely on completion/cancellation)."""
    return {
        "setup": cli._setup.to_dict() if cli._setup else None,
        "calibration": cli._calibration.to_dict() if cli._calibration else None,
        "pending_delete": (
            {"rifle_name": cli._pending_delete, "at": cli._pending_delete_at}
            if cli._pending_delete else None
        ),
    }


@app.post("/v2/voice/query", response_model=VoiceQueryOut)
@limiter.limit("20/minute")
def v2_voice_query(
    request: Request, payload: VoiceQueryIn, user_store: SupabaseProfileStore = Depends(_get_user_store),
):
    """Per-user, per-request BallisticaCLI -- a fresh one every call
    (never a cached object), hydrated from this user's own persisted
    conversation_state before handle() runs and dehydrated back after,
    so the conversation survives across requests (and restarts) the
    same way it did for a single long-lived in-memory CLI object,
    without ever holding two users' state in the same Python object.
    Rate-limited tighter than the blanket default alongside /voice/speak
    and /voice/transcribe above -- unmatched free-text input here can
    fall through to a real Claude API call (see intent.py's LLM
    fallback), a paid per-call cost same as the other two."""
    cli = BallisticaCLI(user_store)
    _hydrate_cli(cli, user_store.get_conversation_state())

    try:
        reply = cli.handle(payload.text)
    except SystemExit:
        reply = "Ending session."
    except (KeyError, ValueError) as exc:
        reply = _msg(exc)

    user_store.set_conversation_state(**_dehydrate_cli(cli))
    awaiting_response = cli._setup is not None or cli._calibration is not None or cli._pending_delete is not None
    return VoiceQueryOut(reply=reply or "Didn't catch that.", awaiting_response=awaiting_response)


# The remaining endpoints the live web UI actually calls (Addendum: live
# app cutover) -- update rifle, add load, status, and the one /calc/*
# route index.html uses (drop-at-range; table/mpbr/angle aren't called
# by the frontend, only by other tooling, so no /v2 versions needed yet).

@app.put("/v2/rifles/{rifle_name}", response_model=RifleDetail)
def v2_update_rifle(
    rifle_name: str, payload: RifleUpdate, user_store: SupabaseProfileStore = Depends(_get_user_store),
):
    try:
        rifle = user_store.update_rifle_fields(rifle_name, **payload.model_dump())
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=_msg(exc))
    user_store.save()
    return _rifle_to_detail(rifle)


@app.post("/v2/rifles/{rifle_name}/loads", response_model=LoadOut)
def v2_add_load(
    rifle_name: str, payload: LoadIn, user_store: SupabaseProfileStore = Depends(_get_user_store),
):
    try:
        rifle = user_store.find_rifle(rifle_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_msg(exc))
    try:
        load = Load(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_msg(exc))
    rifle.add_load(load)
    user_store.save()
    return _load_to_out(load)


@app.get("/v2/status")
def v2_status(user_store: SupabaseProfileStore = Depends(_get_user_store)):
    try:
        rifle = user_store.get_active_rifle()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=_msg(exc))
    active_load = None
    if rifle.active_load_name is not None and rifle.active_load_name in rifle.loads:
        active_load = _load_to_out(rifle.get_active_load())
    return {"rifle": _rifle_to_detail(rifle), "active_load": active_load}


@app.get("/v2/conditions/from-location")
@limiter.limit("20/minute")
def v2_conditions_from_location(
    request: Request, lat: float, lon: float, auth: tuple[str, str] = Depends(_verify_bearer),
):
    """Temperature/humidity/pressure/altitude from the nearest METAR-
    reporting station to the given GPS coordinates (see weather.py).
    Deliberately does NOT return a wind direction -- see that module's
    docstring for why guessing one from GPS alone isn't safe to do.
    Rate-limited alongside the other endpoints that proxy a third-party
    API (here: to avoid this server hammering aviationweather.gov on
    behalf of abusive callers, not a paid-API-cost concern -- their
    API is free)."""
    result = fetch_nearest_station_conditions(lat, lon)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No nearby weather station with usable data found -- enter conditions manually.",
        )
    return {
        "temp_f": result.temp_f, "humidity_pct": result.humidity_pct,
        "pressure_inhg": result.pressure_inhg, "altitude_ft": result.altitude_ft,
        "wind_speed_mph": result.wind_speed_mph, "station_id": result.station_id,
        "station_name": result.station_name, "distance_mi": result.distance_mi,
        "observed_at": result.observed_at,
    }


def _v2_resolve(user_store: SupabaseProfileStore, rifle_query: str | None, load_query: str | None) -> tuple[Rifle, Load]:
    try:
        rifle = user_store.find_rifle(rifle_query) if rifle_query else user_store.get_active_rifle()
        load = rifle.find_load(load_query) if load_query else rifle.get_active_load()
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=_msg(exc))
    return rifle, load


@app.post("/v2/calc/drop-at-range", response_model=RangeReportOut)
def v2_calc_drop_at_range(
    req: DropAtRangeRequest, user_store: SupabaseProfileStore = Depends(_get_user_store),
):
    rifle, load = _v2_resolve(user_store, req.rifle, req.load)
    solver = TrajectorySolver(
        muzzle_velocity_fps=load.muzzle_velocity_fps, bc=load.bc, drag_model=load.drag_model,
        scope_height_in=rifle.scope_height_in,
        atmosphere=req.atmosphere.to_conditions(), wind=req.wind.to_condition(),
    )
    point = solver.at_range(load.zero_distance_yd, req.range_yd)
    return RangeReportOut(**report_for_point(point, rifle.click_value_mrad).__dict__)
