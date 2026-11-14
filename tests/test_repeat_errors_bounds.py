"""Fixes 6-9 from the 2026-11-14 codebase review:

6. "Repeat" works over the API (the last solution survives the per-request
   hydrate/dehydrate round trip).
7. The greeting no longer names Rick.
8. A database failure is a clean JSON error (or a spoken one mid-voice-turn),
   not a bare 500; a validation failure on update is 400, not 404.
9. Numeric inputs are bounded on the API, the voice path and import.

No network: the per-user store is replaced with an in-memory one.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

import ballistica.api as api_module
import ballistica.cli as cli_module
from ballistica.cli import BallisticaCLI, bootstrap_default_profile
from ballistica.import_export import apply_mapping
from ballistica.profiles import ProfileStore


class MemoryStore(ProfileStore):
    """Stands in for SupabaseProfileStore: same surface api.py uses."""

    def __init__(self, path):
        super().__init__(path)
        self.user_id, self.access_token = "user-1", "token-1"
        self.state: dict = {}
        self.fail_save = None       # an exception to raise from save()
        self.fail_state = None      # ... from set_conversation_state()

    def save(self):
        if self.fail_save:
            raise self.fail_save
        super().save()

    def get_conversation_state(self):
        return dict(self.state)

    def set_conversation_state(self, **updates):
        if self.fail_state:
            raise self.fail_state
        self.state.update(updates)

    def log_conversation_turn(self, *a, **k):
        pass


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path / "profiles.json")
    bootstrap_default_profile(s)
    return s


@pytest.fixture
def client(store, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("this turn must not reach the LLM")

    for name in ("extract_intent", "classify_calibration_turn", "extract_setup_fields"):
        monkeypatch.setattr(cli_module, name, boom)
    monkeypatch.setattr(api_module, "contribute_load", lambda *a, **k: None)
    app = api_module.app
    app.dependency_overrides[api_module._verify_bearer] = lambda: ("user-1", "token-1")
    app.dependency_overrides[api_module._get_user_store] = lambda: store
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


def _say(client, text):
    r = client.post("/v2/voice/query", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()["reply"]


_LOAD = {"name": "test", "bullet_weight_gr": 77, "bc": 0.362, "drag_model": "G1",
         "muzzle_velocity_fps": 2700, "zero_distance_yd": 100}
_RIFLE = "AR-15 20in Faxon"


# ------------------------------------------------------------ 6. repeat

def test_repeat_works_across_requests(client):
    solution = _say(client, "drop at 400 yards")
    assert solution.startswith("Solution, 400 yards.")
    assert _say(client, "repeat solution") == solution
    assert _say(client, "repeat the windage").startswith("Windage, ")
    assert _say(client, "repeat elevation").startswith("Elevation, ")


def test_repeat_before_any_solution_still_says_so(client):
    assert "No solution given yet" in _say(client, "repeat solution")


# ---------------------------------------------------------- 7. greeting

def test_greeting_names_nobody(store):
    cli = BallisticaCLI(store)
    assert all("Rick" not in cli.handle("hello") for _ in range(40))


# --------------------------------------------------- 8. database errors

def _unreachable():
    return httpx.ConnectError("no route", request=httpx.Request("POST", "https://db.example/rest"))


def _rejected():
    request = httpx.Request("POST", "https://db.example/rest")
    return httpx.HTTPStatusError("409", request=request, response=httpx.Response(409, request=request))


def test_unreachable_database_is_a_clean_503(client, store):
    store.fail_save = _unreachable()
    r = client.post(f"/v2/rifles/{_RIFLE}/loads", json=_LOAD)
    assert r.status_code == 503
    assert "reach the database" in r.json()["detail"]


def test_rejected_database_write_is_a_clean_502(client, store):
    store.fail_save = _rejected()
    r = client.post(f"/v2/rifles/{_RIFLE}/loads", json=_LOAD)
    assert r.status_code == 502
    assert "nothing was changed" in r.json()["detail"]


def test_failed_save_mid_voice_turn_is_spoken_and_can_be_retried(client, store):
    assert "can't be undone" in _say(client, "delete that load")
    store.fail_save = _unreachable()
    assert "didn't save" in _say(client, "switch to the 21 grain")
    store.fail_save = None
    assert "21.0gr H335" in _say(client, "switch to the 21 grain")


def test_voice_turn_still_answers_when_state_cannot_be_saved(client, store):
    store.fail_state = _unreachable()
    assert _say(client, "drop at 300 yards").startswith("Solution, 300 yards.")


def test_invalid_update_is_400_and_missing_is_404(client):
    rifle = {"scope_height_in": 2.5, "reticle_unit": "FURLONGS"}
    assert client.put(f"/v2/rifles/{_RIFLE}", json=rifle).status_code == 400
    assert client.put("/v2/rifles/no such rifle", json={"scope_height_in": 2.5}).status_code == 404
    bad_load = {**_LOAD, "drag_model": "G9"}
    assert client.put(f"/v2/rifles/{_RIFLE}/loads/21.0gr H335", json=bad_load).status_code == 400
    assert client.put(f"/v2/rifles/{_RIFLE}/loads/nope", json=_LOAD).status_code == 404


# ------------------------------------------------------------- 9. bounds

@pytest.mark.parametrize("field,value", [
    ("zero_distance_yd", 0), ("zero_distance_yd", -100), ("zero_distance_yd", 50000),
    ("bc", 0), ("bc", 9), ("muzzle_velocity_fps", 0), ("muzzle_velocity_fps", 1e9),
    ("bullet_weight_gr", -1), ("powder_charge_gr", -5), ("name", ""), ("name", "x" * 500),
])
def test_out_of_range_load_is_rejected_with_a_readable_message(client, store, field, value):
    r = client.post(f"/v2/rifles/{_RIFLE}/loads", json={**_LOAD, field: value})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert isinstance(detail, str) and field.replace("_", " ") in detail
    assert "test" not in store.rifles[_RIFLE].loads


@pytest.mark.parametrize("raw", ['{"zero_distance_yd": NaN}', '{"muzzle_velocity_fps": 1e999}'])
def test_nan_and_infinity_are_rejected(client, raw):
    import json
    body = json.dumps(_LOAD)[:-1] + ", " + raw[1:]
    r = client.post(f"/v2/rifles/{_RIFLE}/loads", content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_ordinary_load_and_rifle_still_save(client, store):
    assert client.post(f"/v2/rifles/{_RIFLE}/loads", json=_LOAD).status_code == 200
    rifle = {"name": "Bolt gun", "scope_height_in": 1.8, "barrel_length_in": None,
             "objective_lens_mm": None, "dot_size_moa": None}
    assert client.post("/v2/rifles", json=rifle).status_code == 200
    assert "Bolt gun" in store.rifles


@pytest.mark.parametrize("field,value", [("click_value_mrad", 0), ("scope_height_in", -1), ("scope_height_in", 500)])
def test_out_of_range_rifle_is_rejected(client, field, value):
    assert client.put(f"/v2/rifles/{_RIFLE}", json={"scope_height_in": 2.5, field: value}).status_code == 422


@pytest.mark.parametrize("body", [
    {"range_yd": 0}, {"range_yd": -5}, {"range_yd": 1e7},
    {"range_yd": 300, "wind": {"speed_mph": 900}},
    {"range_yd": 300, "wind": {"clock_deg": 720}},
    {"range_yd": 300, "atmosphere": {"temp_f": 900}},
    {"range_yd": 300, "atmosphere": {"humidity_pct": 250}},
])
def test_out_of_range_solve_request_is_rejected(client, body):
    assert client.post("/v2/calc/drop-at-range", json=body).status_code == 422


def test_ordinary_solve_request_still_works(client):
    body = {"range_yd": 300, "wind": {"speed_mph": 10, "clock_deg": 360},
            "atmosphere": {"temp_f": 90, "altitude_ft": 5000, "humidity_pct": 20}}
    r = client.post("/v2/calc/drop-at-range", json=body)
    assert r.status_code == 200 and r.json()["range_yd"] == 300


def test_voice_text_is_length_limited(client):
    assert client.post("/v2/voice/query", json={"text": "x" * 2001}).status_code == 422
    assert client.post("/v2/voice/query", json={"text": "status"}).status_code == 200


@pytest.mark.parametrize("speed,clock", [(1000, 3), (-5, 3), (10, 15), (10, -1)])
def test_implausible_spoken_wind_is_not_applied(store, speed, clock):
    cli = BallisticaCLI(store)
    cli._set_wind(8, 9)
    assert "left the wind alone" in cli._set_wind(speed, clock)
    assert (cli.wind.speed_mph, cli.wind.clock_deg) == (8, 270)


def test_implausible_embedded_wind_is_not_applied(store, monkeypatch):
    cli = BallisticaCLI(store)
    reply = cli.handle("400 yards, wind 900 mph, 3 o'clock")
    assert "left the wind alone" in reply and "Solution, 400 yards." in reply
    assert cli.wind.speed_mph == 0


@pytest.mark.parametrize("kwargs", [
    {"temp_f": 900}, {"pressure_inhg": 2992}, {"humidity_pct": 140}, {"altitude_ft": 250000},
])
def test_implausible_spoken_conditions_are_not_applied(store, kwargs):
    cli = BallisticaCLI(store)
    before = cli.atmosphere
    assert "left the conditions alone" in cli._apply_conditions_update(**kwargs)
    assert cli.atmosphere == before


def test_ordinary_spoken_conditions_still_apply(store):
    cli = BallisticaCLI(store)
    assert "Conditions updated" in cli._apply_conditions_update(temp_f=90, altitude_ft=5200)
    assert cli.atmosphere.temp_f == 90


@pytest.mark.parametrize("column,value", [("zero", "-50"), ("zero", "90000"), ("mv", "99999"), ("bc", "7")])
def test_import_row_out_of_range_fails_that_row(column, value):
    row = {"rifle": "Imported", "load": "L1", "bc": "0.4", "mv": "2700", "zero": "100", column: value}
    mapping = {"rifle_name": "rifle", "load_name": "load", "bc": "bc",
               "muzzle_velocity_fps": "mv", "zero_distance_yd": "zero"}
    results, touched = apply_mapping([row], mapping, existing_rifles={})
    assert results[0].status == "failed" and "out of range" in results[0].detail
    assert not touched
