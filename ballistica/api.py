"""HTTP API wrapping the ballistics engine, so the phone app (field
use, voice) and web app (data entry/review) can share one backend
instead of each reimplementing the physics.

State model: single-user local tool (Rick), not multi-tenant SaaS, so
this keeps the same "one active rifle/load" model the CLI already
uses, backed by the same ProfileStore/data/profiles.json. What's
deliberately NOT global state: atmosphere and wind. Those come in on
every calculation request instead of being "set once" server-side,
because the phone app will have fresh GPS+weather data on every call
-- baking that into mutable server state would just create a race
between the phone and web clients hitting the same backend.

Run with: uvicorn ballistica.api:app --reload
Interactive docs at http://127.0.0.1:8000/docs once it's running.
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

# Loads .env (gitignored, holds OPENAI_API_KEY) into the process
# environment before anything below reads it -- must run before the
# OpenAI import/client construction, not after.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import ssl

import certifi
import httpx2
import openai
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .angle import solve_incline_angle
from .atmosphere import AtmosphereConditions, STANDARD_ATMOSPHERE, pressure_at_altitude_inhg
from .cli import BallisticaCLI, bootstrap_default_profile
from .profiles import Load, ProfileStore, Rifle
from .reporting import report_for_point, report_table
from .trajectory import TrajectorySolver, WindCondition
from .zero import find_minimum_spread_zero

app = FastAPI(title="Ballistica API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten once the web/phone clients have real origins
    allow_methods=["*"],
    allow_headers=["*"],
)

store = ProfileStore()
if not store.rifles:
    # Render's free tier has no persistent disk -- data/profiles.json
    # (correctly gitignored, since it's local runtime data, not source)
    # won't exist on a fresh container, and won't survive a redeploy
    # even if written at runtime. Falling back to the same bootstrap
    # profile the CLI uses on first run, so the deployed app is never
    # stuck with an empty rifle list.
    bootstrap_default_profile(store)

# One shared voice session for the whole server, deliberately -- unlike
# the /calc/* endpoints (which take atmosphere/wind per-request so
# concurrent phone+web clients can't race on shared state), a voice
# conversation genuinely needs continuity: "it's about 90 out, wind 5
# from the west" said once should still apply when you ask for a drop
# a minute later. This is a personal single-user tool used by one
# person at a time in the field, so one shared conversation state is
# the right call here, not a bug -- same reasoning the CLI itself
# already relies on (it's this exact class).
voice_cli = BallisticaCLI(store)

_WEB_DIR = Path(__file__).resolve().parent / "web"
_WEB_INDEX = _WEB_DIR / "index.html"

app.mount("/icons", StaticFiles(directory=_WEB_DIR / "icons"), name="icons")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def web_ui():
    return _WEB_INDEX.read_text(encoding="utf-8")


@app.get("/manifest.json", include_in_schema=False)
def web_manifest():
    return FileResponse(_WEB_DIR / "manifest.json", media_type="application/manifest+json")


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
    reticle_unit: str = Field("MRAD", description="'MRAD' or 'MOA' -- which unit the scope's turrets use")
    scope_make: str = ""
    scope_model: str = ""
    magnification: str = ""
    objective_lens_mm: float | None = None
    focal_plane: str = Field("", description="'FFP', 'SFP', or '' if unknown -- not used by today's "
                                              "turret-dial calculations, but will matter once a "
                                              "reticle-holdover or rangefinder feature exists")
    reticle_type: str = ""
    loads: list[LoadIn] = []


class RifleUpdate(BaseModel):
    """Full replace of a rifle's editable metadata (not its loads)."""
    scope_height_in: float
    caliber: str = ""
    barrel_length_in: float | None = None
    twist_rate: str = ""
    click_value_mrad: float = 0.1
    reticle_unit: str = "MRAD"
    scope_make: str = ""
    scope_model: str = ""
    magnification: str = ""
    objective_lens_mm: float | None = None
    focal_plane: str = ""
    reticle_type: str = ""


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
    scope_make: str
    scope_model: str
    magnification: str
    objective_lens_mm: float | None
    focal_plane: str
    reticle_type: str
    active_load_name: str | None
    loads: list[LoadOut]


class VelocityUpdate(BaseModel):
    muzzle_velocity_fps: float


class CalcRequest(BaseModel):
    rifle: str | None = Field(None, description="Fuzzy match; omit to use the active rifle")
    load: str | None = Field(None, description="Fuzzy match; omit to use the rifle's active load")
    atmosphere: AtmosphereIn = AtmosphereIn()
    wind: WindIn = WindIn()


class DropAtRangeRequest(CalcRequest):
    range_yd: float


class DropTableRequest(CalcRequest):
    max_range_yd: float
    step_yd: float = 100.0


class MpbrRequest(CalcRequest):
    max_range_yd: float


class AngleRequest(CalcRequest):
    reference_distance_yd: float = 100.0
    line_of_sight_distance_yd: float
    observed_diff_clicks: float


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


class SpreadResultOut(BaseModel):
    zero_distance_yd: float
    max_height_in: float
    min_height_in: float
    spread_in: float


class AngleResultOut(BaseModel):
    angle_deg: float
    shoot_to_distance_yd: float
    line_of_sight_distance_yd: float
    level_ground_diff_clicks: float
    observed_diff_clicks: float
    corrected_holdover_clicks: float


class VoiceQueryIn(BaseModel):
    text: str = Field(description="Transcribed speech, e.g. \"what's my drop at 500 yards\"")


class VoiceQueryOut(BaseModel):
    reply: str = Field(description="Text meant to be spoken back via TTS -- terse and numeric by design")


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


# ------------------------------------------------------------------ helpers

def _msg(exc: Exception) -> str:
    """KeyError.__str__ wraps its message in an extra repr() layer (so
    str(KeyError("no rifle")) == '"no rifle"', quotes and all) -- this
    unwraps that so API error details read as plain text either way."""
    return exc.args[0] if exc.args else str(exc)


_openai_client: openai.OpenAI | None = None


def _get_openai_client() -> openai.OpenAI:
    """Lazily builds (and caches) the OpenAI client.

    httpx2/httpcore2's default SSL context construction routes through
    `truststore` for native OS certificate-store integration, which has
    a confirmed infinite-recursion bug (RecursionError) against Python
    3.14's ssl module as of truststore 0.10.4 -- reproduced directly,
    not assumed. Supplying our own pre-built context via certifi's CA
    bundle instead of letting httpcore2 construct its default one skips
    that code path entirely. This is a workaround for an upstream
    library bug, not a security downgrade: it still verifies against a
    real, current CA bundle.

    Lazy + cached rather than built at import time so importing this
    module doesn't hard-fail in environments with no OPENAI_API_KEY set
    (tests, or Render before the env var is configured).
    """
    global _openai_client
    if _openai_client is None:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        http_client = httpx2.Client(verify=ssl_context)
        _openai_client = openai.OpenAI(http_client=http_client)
    return _openai_client


def _load_to_out(load: Load) -> LoadOut:
    return LoadOut(**load.__dict__)


def _rifle_to_detail(rifle: Rifle) -> RifleDetail:
    return RifleDetail(
        name=rifle.name, scope_height_in=rifle.scope_height_in, caliber=rifle.caliber,
        barrel_length_in=rifle.barrel_length_in, twist_rate=rifle.twist_rate,
        click_value_mrad=rifle.click_value_mrad, reticle_unit=rifle.reticle_unit,
        scope_make=rifle.scope_make, scope_model=rifle.scope_model,
        magnification=rifle.magnification, objective_lens_mm=rifle.objective_lens_mm,
        focal_plane=rifle.focal_plane, reticle_type=rifle.reticle_type,
        active_load_name=rifle.active_load_name,
        loads=[_load_to_out(load) for load in rifle.loads.values()],
    )


def _resolve(rifle_query: str | None, load_query: str | None) -> tuple[Rifle, Load]:
    try:
        rifle = store.find_rifle(rifle_query) if rifle_query else store.get_active_rifle()
        load = rifle.find_load(load_query) if load_query else rifle.get_active_load()
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=_msg(exc))
    return rifle, load


def _solver_for(req: CalcRequest) -> tuple[TrajectorySolver, Rifle, Load]:
    rifle, load = _resolve(req.rifle, req.load)
    solver = TrajectorySolver(
        muzzle_velocity_fps=load.muzzle_velocity_fps,
        bc=load.bc,
        drag_model=load.drag_model,
        scope_height_in=rifle.scope_height_in,
        atmosphere=req.atmosphere.to_conditions(),
        wind=req.wind.to_condition(),
    )
    return solver, rifle, load


# ----------------------------------------------------------------- routes

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/rifles", response_model=list[RifleSummary])
def list_rifles():
    return [
        RifleSummary(name=r.name, active_load_name=r.active_load_name, load_count=len(r.loads))
        for r in store.rifles.values()
    ]


@app.post("/rifles", response_model=RifleDetail)
def create_rifle(payload: RifleIn):
    if payload.name in store.rifles:
        raise HTTPException(status_code=409, detail=f"Rifle '{payload.name}' already exists")
    try:
        rifle = Rifle(
            name=payload.name, scope_height_in=payload.scope_height_in,
            caliber=payload.caliber, barrel_length_in=payload.barrel_length_in,
            twist_rate=payload.twist_rate, click_value_mrad=payload.click_value_mrad,
            reticle_unit=payload.reticle_unit, scope_make=payload.scope_make,
            scope_model=payload.scope_model, magnification=payload.magnification,
            objective_lens_mm=payload.objective_lens_mm, focal_plane=payload.focal_plane,
            reticle_type=payload.reticle_type,
        )
        for i, load_in in enumerate(payload.loads):
            rifle.add_load(Load(**load_in.model_dump()), make_active=(i == 0))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_msg(exc))
    store.add_rifle(rifle)
    store.save()
    return _rifle_to_detail(rifle)


@app.get("/rifles/{rifle_name}", response_model=RifleDetail)
def get_rifle(rifle_name: str):
    rifle, _ = _resolve(rifle_name, None)
    return _rifle_to_detail(rifle)


@app.put("/rifles/{rifle_name}", response_model=RifleDetail)
def update_rifle(rifle_name: str, payload: RifleUpdate):
    """Full replace of a rifle's editable metadata, e.g. correcting
    twist rate or scope height after the fact. Does not touch loads."""
    try:
        rifle = store.update_rifle_fields(rifle_name, **payload.model_dump())
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=_msg(exc))
    store.save()
    return _rifle_to_detail(rifle)


@app.post("/rifles/{rifle_name}/active", response_model=RifleDetail)
def set_active_rifle(rifle_name: str):
    try:
        rifle = store.set_active_rifle(rifle_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_msg(exc))
    store.save()
    return _rifle_to_detail(rifle)


@app.post("/rifles/{rifle_name}/loads", response_model=LoadOut)
def add_load(rifle_name: str, payload: LoadIn):
    rifle, _ = _resolve(rifle_name, None)
    try:
        load = Load(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_msg(exc))
    rifle.add_load(load)
    store.save()
    return _load_to_out(load)


@app.post("/rifles/{rifle_name}/loads/{load_name}/active", response_model=LoadOut)
def set_active_load(rifle_name: str, load_name: str):
    rifle, _ = _resolve(rifle_name, None)
    try:
        load = rifle.find_load(load_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_msg(exc))
    rifle.active_load_name = load.name
    store.save()
    return _load_to_out(load)


@app.patch("/rifles/{rifle_name}/loads/{load_name}/velocity", response_model=LoadOut)
def update_velocity(rifle_name: str, load_name: str, payload: VelocityUpdate):
    try:
        load = store.update_load_velocity(rifle_name, load_name, payload.muzzle_velocity_fps)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=_msg(exc))
    store.save()
    return _load_to_out(load)


@app.get("/status")
def status():
    try:
        rifle = store.get_active_rifle()
        load = rifle.get_active_load()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=_msg(exc))
    return {"rifle": _rifle_to_detail(rifle), "active_load": _load_to_out(load)}


@app.post("/voice/query", response_model=VoiceQueryOut)
def voice_query(payload: VoiceQueryIn):
    """The one seam a future STT->this->TTS voice loop needs: transcribed
    text in, spoken-back text out. Reuses BallisticaCLI.handle() verbatim
    so voice and the text CLI understand queries identically -- no second
    copy of the "what did they actually ask for" parsing logic to drift
    out of sync with the first."""
    try:
        reply = voice_cli.handle(payload.text)
    except SystemExit:
        # handle() raises this for "quit"/"exit" in the REPL, where it's
        # correct -- exiting the process. Here there is no process-per-
        # conversation to exit, just acknowledge and keep the server up.
        reply = "Ending session."
    except (KeyError, ValueError) as exc:
        # A handler hit a real error (e.g. unreachable angle-solve
        # inputs) -- speak it back rather than surfacing a raw 500 to
        # whatever's about to pipe this through TTS.
        reply = _msg(exc)
    return VoiceQueryOut(reply=reply or "Didn't catch that.")


@app.post("/voice/speak")
def voice_speak(payload: VoiceSpeakIn):
    """Text in, MP3 bytes out via OpenAI TTS. The other half of the
    voice loop from /voice/query -- feed that endpoint's reply straight
    into this one to get spoken audio back."""
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text must not be empty")
    try:
        client = _get_openai_client()
        result = client.audio.speech.create(
            model="tts-1", voice=payload.voice, input=text, speed=payload.speed,
        )
    except openai.OpenAIError as exc:
        raise HTTPException(status_code=502, detail=f"TTS request failed: {exc}")
    return Response(content=result.content, media_type="audio/mpeg")


@app.post("/voice/transcribe")
async def voice_transcribe(audio: UploadFile = File(...)):
    """Recorded audio in (whatever format the browser's MediaRecorder
    produced -- webm/ogg/mp4 are all fine, OpenAI's transcription
    endpoint handles the common ones), transcribed text out. First
    third of the full voice loop: this -> /voice/query -> /voice/speak."""
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="audio file was empty")
    try:
        client = _get_openai_client()
        # OpenAI's SDK identifies the audio format from the filename's
        # extension, not the content-type header, so this needs a real
        # name attached, not just raw bytes.
        file_tuple = (audio.filename or "recording.webm", audio_bytes, audio.content_type or "audio/webm")
        result = client.audio.transcriptions.create(model="whisper-1", file=file_tuple)
    except openai.OpenAIError as exc:
        raise HTTPException(status_code=502, detail=f"Transcription failed: {exc}")
    return {"text": result.text}


@app.post("/calc/drop-at-range", response_model=RangeReportOut)
def calc_drop_at_range(req: DropAtRangeRequest):
    solver, rifle, load = _solver_for(req)
    point = solver.at_range(load.zero_distance_yd, req.range_yd)
    return RangeReportOut(**report_for_point(point, rifle.click_value_mrad).__dict__)


@app.post("/calc/drop-table", response_model=list[RangeReportOut])
def calc_drop_table(req: DropTableRequest):
    solver, rifle, load = _solver_for(req)
    points = solver.drop_table(load.zero_distance_yd, req.max_range_yd, req.step_yd)
    return [RangeReportOut(**r.__dict__) for r in report_table(points, rifle.click_value_mrad)]


@app.post("/calc/mpbr-zero", response_model=SpreadResultOut)
def calc_mpbr_zero(req: MpbrRequest):
    solver, _, _ = _solver_for(req)
    result = find_minimum_spread_zero(solver, req.max_range_yd)
    return SpreadResultOut(**result.__dict__)


@app.post("/calc/angle", response_model=AngleResultOut)
def calc_angle(req: AngleRequest):
    solver, rifle, load = _solver_for(req)
    try:
        result = solve_incline_angle(
            solver, load.zero_distance_yd, req.reference_distance_yd,
            req.line_of_sight_distance_yd, req.observed_diff_clicks, rifle.click_value_mrad,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=_msg(exc))
    holdover = result.corrected_holdover_clicks(
        solver, load.zero_distance_yd, req.line_of_sight_distance_yd, rifle.click_value_mrad,
    )
    return AngleResultOut(**result.__dict__, corrected_holdover_clicks=holdover)
