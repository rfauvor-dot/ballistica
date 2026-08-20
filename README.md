# Ballistica

Voice-operated ballistic calculator engine. Phase 1 (exterior ballistics
solver + rifle/load profiles) and Phase 2 (incline/angle solver) from
the technical brief. No avatar, no speech I/O yet -- see "What's not
here" below.

## Layout

```
ballistica/
  drag_tables.py   G1/G7 standard drag tables (Mach vs Cd), interpolation
  atmosphere.py     air density / speed of sound from temp/pressure/humidity/altitude
  trajectory.py     RK4 point-mass solver, zero-angle solve, drop/windage tables
  zero.py           minimum-vertical-spread ("generalized MPBR") zero optimizer
  angle.py          Phase 2: back-calculate incline angle from a field observation
  profiles.py       persistent rifle/load storage (JSON), fuzzy voice-style lookup
  reporting.py       inches / MOA / MRAD / click formatting
  units.py          unit conversion constants and helpers
  cli.py             text REPL exercising the intended voice query patterns
  api.py             HTTP API wrapping the engine (see "API" below)
tests/test_engine.py  validation suite (see below)
data/profiles.json    profile storage (created on first run, gitignored)
```

## Running it

```bash
pip install -r requirements.txt
python -m ballistica.cli
```

First run with no `data/profiles.json` auto-loads the brief's validation
test case (AR-15 20in Faxon, 77gr SMK, 21.0gr/23.5gr H335 loads, 36yd
zero). Type `help` at the prompt for supported queries.

Run the test suite: `python -m pytest tests/ -v`

## API

The phone app (field use, voice) and web app (data entry/review) are
meant to share this one backend instead of each reimplementing the
physics -- see `ballistica/api.py`'s module docstring for the state
model (single active rifle/load like the CLI; atmosphere/wind are
passed per-request, not stored server-side, since a phone app will
have fresh GPS+weather on every call).

```bash
python -m uvicorn ballistica.api:app --host 127.0.0.1 --port 8010 --reload
```

Then open `http://127.0.0.1:8010/docs` for interactive Swagger docs --
every endpoint below is callable straight from the browser, no client
needed yet.

**Gotcha on this machine:** port 8000 (uvicorn's default) is in
Windows' excluded port range here, probably reserved for Hyper-V/WSL
(`netsh interface ipv4 show excludedportrange protocol=tcp` will show
it). Binding it fails with `WinError 10013`, and something else on the
box answers with a generic IIS-style 404 instead of a connection
error, which is a confusing failure mode if you hit it fresh. Port
8010 is free; check `netstat -ano` before picking another one.

Endpoints:
- `GET /rifles`, `POST /rifles`, `GET /rifles/{name}` -- profile CRUD (fuzzy name match)
- `POST /rifles/{name}/active`, `POST /rifles/{name}/loads/{load}/active` -- switch active rifle/load
- `POST /rifles/{name}/loads` -- add a load; `PATCH .../loads/{load}/velocity` -- update just the velocity
- `GET /status` -- current active rifle/load
- `POST /calc/drop-at-range`, `/calc/drop-table`, `/calc/mpbr-zero`, `/calc/angle` -- the four engine calculations, each taking optional `rifle`/`load` (default: active) and optional `atmosphere`/`wind` (default: standard atmosphere, no wind)

## Accuracy: how this was validated, and what's still approximate

The G1/G7 drag tables were pulled from source (not recalled from
memory) and cross-checked row-count and content against the raw
py-ballisticcalc source file. The core drag-deceleration constant was
independently re-derived from first principles (sectional density,
form factor, standard atmosphere) and matched that same open-source
library's implementation to 5 significant figures. Two full trajectory
scenarios (standard atmosphere; and a non-standard atmosphere +
crosswind case) were run through both engines side by side and match
to within ~0.05 in of drop and ~0.02 in of windage out to 500 yards --
see `tests/test_engine.py` for the exact reference numbers.

What's still approximate or needs Rick's own data:
- **The 77gr SMK BC in the demo profile (0.362 G1)** is Sierra's own
  published book value for the 1700-3000 fps band, cross-checked
  directly against Sierra's ballistics coefficient table. Sierra
  publishes this bullet as G1 only -- no G7 figure exists on their
  sheet. A Litz/Applied-Ballistics-measured G7 BC, or better yet a
  Doppler/chronograph-derived custom BC from Rick's own loads, would
  improve transonic-range accuracy. Swapping it is a one-field change
  on the `Load`.
- **Muzzle velocities (2422/2766 fps)** are book/manual estimates, not
  measured. `ProfileStore.update_load_velocity()` exists specifically
  so a chronograph number can replace them without re-entering
  anything else.
- **The MPBR/minimum-spread zero solver** solves literally what the
  brief describes: balance peak rise above line of sight against drop
  at the stated max range, with no vital-zone-radius constraint. That
  will generally produce a *longer* zero than a traditional
  fixed-radius MPBR table (e.g. "stay within 3in of POA") would. If
  Rick actually wants a fixed-tolerance version, that's a small
  addition to `zero.py`, not a redesign.

## Range-ready web page (tablet, tonight's build)

`ballistica/web/index.html` is a single self-contained mobile page --
select rifle/load (fuzzy names reused from the profile store), enter a
distance, get drop and windage back in inches/MRAD/clicks, with an
optional collapsed section for conditions/wind. Served directly by the
API at `GET /` (same origin as the calc endpoints, so no CORS to
worry about) -- just open the server's URL on the tablet.

**Getting a public URL for the tablet (its own cell connection, not
home wifi):** this repo has no git remote and no Render account
connected yet, so standing up a persistent host tonight wasn't
realistic without someone doing that account setup by hand. Used a
Cloudflare quick tunnel instead (`bin/cloudflared.exe tunnel --url
http://127.0.0.1:8010`, gitignored, downloaded from Cloudflare's
official GitHub releases) -- no account or credentials needed, and it
worked end to end through Cloudflare's actual edge network, not just
loopback.

```bash
python -m uvicorn ballistica.api:app --host 127.0.0.1 --port 8010 &
./bin/cloudflared.exe tunnel --url http://127.0.0.1:8010
```

The printed `https://<random-words>.trycloudflare.com` URL is what
goes on the tablet.

**Real limitations of this path, not just a data caveat:**
- It only stays up as long as this laptop is on, on the network, and
  both processes keep running. If the machine sleeps or restarts
  overnight, the URL goes dead -- worth checking it's still live
  first thing before heading out, and worth turning off sleep on this
  machine for the night (that's a system setting, so that's a manual
  step, not something done for you here).
- The URL is randomly generated and not stable -- restarting the
  tunnel for any reason issues a *new* URL, so a bookmark saved
  tonight could break silently.
- No authentication on the tunnel. The random subdomain isn't
  discoverable, but there's no password gate either. Fine for one
  night; not something to leave running long-term.
- Cloudflare's own quick-tunnel terms are explicit that these have no
  uptime guarantee and aren't meant for production use.

None of that is a reason to avoid it for tonight -- it's the fastest
path to a real public URL with zero account setup. It is the reason a
Render deployment (needs a git remote and Rick's/DT's Render account
connected -- account creation and login aren't something to do on
someone else's behalf) is worth doing properly once this proves out,
rather than running on a tunnel indefinitely.

## What's not here

- Live voice correction loop (section 5's hands-free "call out the
  miss, get the correction back" flow) -- TTS re-uses Rick's existing
  ElevenLabs pattern fairly directly once wired up, but STT (capturing
  a spoken miss distance/direction hands-free) is real, untested work:
  streaming mic capture, a recognition service, and push-to-talk vs.
  wake-word UX, on top of whichever client ends up being the field app.
- GPS/weather auto-fill (sections 2-3) -- the web page takes
  conditions as manual optional input for now; wiring an actual
  browser Geolocation + weather API call in is a natural next step on
  the same page, not a rebuild.
- Session history logging, CSV import, historical cross-referencing
  (sections 7, 11, 12) -- not built yet; the API's shape (rifle/load
  keyed, JSON-backed) extends naturally to it when it's next.
- No billing.
