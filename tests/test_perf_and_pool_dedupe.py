"""Fixes 10-12 from the 2026-11-14 codebase review:

10. Supabase calls share one kept-alive HTTP client, and a request reads the
    conversation-state row once instead of three times.
11. The solver is faster without changing a single result.
12. A save that changes nothing the aggregate pool records contributes
    nothing.

No network.
"""
from __future__ import annotations

import httpx
import pytest

import ballistica.aggregate_pool as pool
import ballistica.supabase_store as store_module
import ballistica.trajectory as trajectory
from ballistica.profiles import Load, Rifle
from ballistica.trajectory import TrajectorySolver, WindCondition, _interp_point_at_range

from test_safe_save_and_zero_reach import FakeDB, FakeStore


# ------------------------------------------------- 10. connection + reads

def test_every_supabase_call_reuses_one_client():
    assert store_module.supabase_http() is store_module.supabase_http()
    assert isinstance(store_module.supabase_http(), httpx.Client)


def test_store_requests_go_through_the_shared_client(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(store_module, "supabase_http", lambda: client)
    monkeypatch.setattr(store_module, "_SUPABASE_URL", "https://db.example")
    store = store_module.SupabaseProfileStore("user-1", "token")
    store.get_conversation_state()
    store.set_conversation_state(wind={"speed_mph": 5})
    assert [path.rsplit("/", 1)[-1] for _, path in seen] == [
        "rifles", "loads", "conversation_state", "conversation_state",
    ]
    assert [m for m, _ in seen] == ["GET", "GET", "GET", "POST"]


def test_conversation_state_is_read_once_per_store():
    db = FakeDB()
    store = FakeStore(db)
    store.set_conversation_state(pending={"x": 1})
    assert store.get_conversation_state()["pending"] == {"x": 1}
    store.set_conversation_state(wind={"speed_mph": 5})
    assert db.calls.count(("GET", "conversation_state")) == 1
    # Both keys survived, and a new store sees what was written.
    assert set(FakeStore(db).get_conversation_state()) >= {"pending", "wind"}


def test_returned_state_is_a_copy():
    store = FakeStore(FakeDB())
    store.get_conversation_state()["scribble"] = True
    assert "scribble" not in store.get_conversation_state()


# ----------------------------------------------------------- 11. solver

_CASES = [
    dict(muzzle_velocity_fps=2766, bc=0.362, drag_model="G1", scope_height_in=2.5),
    dict(muzzle_velocity_fps=2950, bc=0.31, drag_model="G7", scope_height_in=1.9,
         wind=WindCondition(speed_mph=12, clock_deg=90)),
    dict(muzzle_velocity_fps=1050, bc=0.15, drag_model="G1", scope_height_in=2.6),
]


@pytest.mark.parametrize("kwargs", _CASES)
@pytest.mark.parametrize("range_yd", [25, 300, 800])
def test_fast_path_matches_the_full_trajectory_exactly(kwargs, range_yd):
    """at_range() no longer builds the whole trajectory; it must give exactly
    what interpolating the whole trajectory gives."""
    solver = TrajectorySolver(**kwargs)
    full = solver.trajectory(50, range_yd + 10.0)
    assert solver.at_range(50, range_yd) == _interp_point_at_range(full, range_yd * 3.0)


def test_known_values_are_unchanged():
    """Pinned from the solver before the 2026-11-14 speed-up (bit-for-bit)."""
    p = TrajectorySolver(muzzle_velocity_fps=2766, bc=0.362, drag_model="G1", scope_height_in=2.5).at_range(36, 500)
    assert (p.drop_ft, p.velocity_fps, p.time_s) == (-3.738933965605801, 1644.3312326472399, 0.7049223738150064)
    p = TrajectorySolver(muzzle_velocity_fps=1050, bc=0.15, drag_model="G1", scope_height_in=2.5).at_range(25, 100)
    assert (p.drop_ft, p.velocity_fps, p.time_s) == (-0.48892030395028563, 918.9855936463898, 0.3069289194870061)


def test_zero_angle_is_solved_once_per_load_and_conditions(monkeypatch):
    trajectory._ZERO_ANGLE_CACHE.clear()
    solver = TrajectorySolver(muzzle_velocity_fps=2700, bc=0.4, drag_model="G1", scope_height_in=2.0)
    calls = []
    original = TrajectorySolver._point_at
    monkeypatch.setattr(TrajectorySolver, "_point_at",
                        lambda self, angle, x: calls.append(x) or original(self, angle, x))
    first = solver.at_range(100, 300)
    searched = len(calls)
    assert searched > 5                      # the zero search ran
    solver.at_range(100, 400)
    assert len(calls) == searched + 1        # second range: just the one trajectory
    # A different condition is a different zero angle, not a stale hit.
    windy = TrajectorySolver(muzzle_velocity_fps=2700, bc=0.4, drag_model="G1", scope_height_in=2.0,
                             wind=WindCondition(speed_mph=20, clock_deg=0))
    windy.at_range(100, 300)
    assert len(calls) > searched + 2
    assert solver.at_range(100, 300) == first


def test_zero_angle_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(trajectory, "_ZERO_ANGLE_CACHE_MAX", 3)
    trajectory._ZERO_ANGLE_CACHE.clear()
    for mv in (2500, 2600, 2700, 2800, 2900):
        TrajectorySolver(muzzle_velocity_fps=mv, bc=0.4, drag_model="G1", scope_height_in=2.0).solve_zero_angle(50)
    assert len(trajectory._ZERO_ANGLE_CACHE) <= 3


# ------------------------------------------------------- 12. pool dedupe

@pytest.fixture
def sent(monkeypatch):
    posts = []

    class _Client:
        def post(self, url, **kwargs):
            posts.append(kwargs["json"]["payload"])
            return httpx.Response(201, request=httpx.Request("POST", url))

    monkeypatch.setattr(pool, "supabase_http", lambda: _Client())
    monkeypatch.setattr(pool, "_SUPABASE_URL", "https://db.example")
    return posts


def _pair():
    rifle = Rifle(name="AR", scope_height_in=2.5, caliber=".223", barrel_length_in=20)
    load = Load(name="21gr", bullet_weight_gr=77, bc=0.362, drag_model="G1",
                muzzle_velocity_fps=2422, zero_distance_yd=36, powder="H335", powder_charge_gr=21.0)
    return rifle, load


def test_new_load_is_contributed(sent):
    rifle, load = _pair()
    pool.contribute_load(rifle, load, "token")
    assert len(sent) == 1


def test_rename_or_notes_edit_contributes_nothing(sent):
    rifle, load = _pair()
    before = pool.build_load_event_payload(rifle, load)
    load.name, load.notes = "21.0gr H335", "shot well in the cold"
    pool.contribute_load(rifle, load, "token", previous=before)
    assert sent == []


def test_a_real_change_is_contributed(sent):
    rifle, load = _pair()
    before = pool.build_load_event_payload(rifle, load)
    load.muzzle_velocity_fps = 2440
    pool.contribute_load(rifle, load, "token", previous=before)
    assert len(sent) == 1 and sent[0]["muzzle_velocity_fps"] == 2440


def test_api_passes_the_before_state(tmp_path, monkeypatch):
    """PUT with identical data: one contribution for the create, none for the
    no-op update, one for a real change."""
    import ballistica.api as api_module
    from fastapi.testclient import TestClient
    from test_repeat_errors_bounds import MemoryStore

    store = MemoryStore(tmp_path / "profiles.json")
    store.add_rifle(Rifle(name="AR", scope_height_in=2.5))
    calls = []
    monkeypatch.setattr(api_module, "contribute_load",
                        lambda rifle, load, token, previous=None: calls.append(
                            previous != pool.build_load_event_payload(rifle, load)))
    app = api_module.app
    app.dependency_overrides[api_module._verify_bearer] = lambda: ("user-1", "token-1")
    app.dependency_overrides[api_module._get_user_store] = lambda: store
    try:
        client = TestClient(app)
        body = {"name": "L", "bullet_weight_gr": 77, "bc": 0.362, "drag_model": "G1",
                "muzzle_velocity_fps": 2700, "zero_distance_yd": 100}
        assert client.post("/v2/rifles/AR/loads", json=body).status_code == 200
        assert client.put("/v2/rifles/AR/loads/L", json={**body, "notes": "edit"}).status_code == 200
        assert client.put("/v2/rifles/AR/loads/L", json={**body, "muzzle_velocity_fps": 2725}).status_code == 200
    finally:
        app.dependency_overrides.clear()
    assert calls == [True, False, True]   # True = would actually send
