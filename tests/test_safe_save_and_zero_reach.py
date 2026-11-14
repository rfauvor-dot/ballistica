"""Two fixes from the 2026-11-14 codebase review:

1. SupabaseProfileStore.save() used to delete every rifle the user had and
   re-insert them all on every call. It now writes only what changed. These
   tests run against a small in-memory stand-in for the Supabase REST API (no
   network), and assert on the exact requests save() makes.
2. The zero solver looped forever on a zero distance the bullet can't reach.
"""
from __future__ import annotations

import itertools
import threading

import pytest

from ballistica.profiles import Load, Rifle
from ballistica.supabase_store import SupabaseProfileStore
from ballistica.trajectory import TrajectorySolver


class _Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeDB:
    def __init__(self):
        self.tables = {"rifles": [], "loads": [], "conversation_state": []}
        self._ids = itertools.count(1)
        self.calls: list[tuple[str, str]] = []
        self.fail_on: tuple[str, str] | None = None

    def _matching(self, table, params):
        rows = self.tables[table]
        for key, value in (params or {}).items():
            if isinstance(value, str) and value.startswith("eq."):
                rows = [r for r in rows if str(r.get(key)) == value[3:]]
        return rows

    def request(self, method, table, params=None, json=None):
        self.calls.append((method, table))
        if self.fail_on == (method, table):
            raise RuntimeError(f"simulated failure on {method} {table}")
        if method == "GET":
            return _Resp([dict(r) for r in self._matching(table, params)])
        if method == "POST":
            if table == "conversation_state":
                self.tables[table] = [r for r in self.tables[table] if r["user_id"] != json["user_id"]] + [dict(json)]
                return _Resp([])
            new = []
            for item in json if isinstance(json, list) else [json]:
                row = {"id": f"{table}-{next(self._ids)}", "active_load_id": None, **item}
                self.tables[table].append(row)
                new.append(dict(row))
            return _Resp(new)
        if method == "PATCH":
            for row in self._matching(table, params):
                row.update(json)
            return _Resp([])
        if method == "DELETE":
            doomed = {r["id"] for r in self._matching(table, params)}
            self.tables[table] = [r for r in self.tables[table] if r["id"] not in doomed]
            if table == "rifles":
                self.tables["loads"] = [l for l in self.tables["loads"] if l["rifle_id"] not in doomed]
            else:
                for r in self.tables["rifles"]:
                    if r["active_load_id"] in doomed:
                        r["active_load_id"] = None
            return _Resp([])
        raise AssertionError(method)


class FakeStore(SupabaseProfileStore):
    def __init__(self, db: FakeDB):
        self.db = db
        super().__init__("user-1", "token")

    def _rest(self, method, table, headers=None, **kwargs):
        return self.db.request(method, table, kwargs.get("params"), kwargs.get("json"))


def _load(name, mv=2700.0):
    return Load(name=name, bullet_weight_gr=77, bc=0.362, drag_model="G1",
                muzzle_velocity_fps=mv, zero_distance_yd=100)


@pytest.fixture
def db():
    """Two rifles, two loads on the first, saved through the store itself."""
    db = FakeDB()
    store = FakeStore(db)
    ar = Rifle(name="AR-15", scope_height_in=2.5)
    ar.add_load(_load("21gr"))
    ar.add_load(_load("23gr"))
    store.add_rifle(ar)
    store.add_rifle(Rifle(name="Bolt gun", scope_height_in=1.8))
    store.save()
    db.calls.clear()
    return db


def _ids(db):
    return ({r["name"]: r["id"] for r in db.tables["rifles"]},
            {l["name"]: l["id"] for l in db.tables["loads"]})


def _writes(db):
    return [c for c in db.calls if c[0] != "GET"]


def test_save_round_trips(db):
    store = FakeStore(db)
    assert set(store.rifles) == {"AR-15", "Bolt gun"}
    assert set(store.rifles["AR-15"].loads) == {"21gr", "23gr"}
    assert store.rifles["AR-15"].active_load_name == "23gr"
    assert store.active_rifle_name == "Bolt gun"


def test_save_with_no_changes_writes_nothing(db):
    store = FakeStore(db)
    db.calls.clear()
    store.save()
    assert _writes(db) == []


def test_switching_the_active_load_deletes_nothing_and_keeps_every_id(db):
    before = _ids(db)
    store = FakeStore(db)
    store.set_active_rifle("AR-15")
    store.set_active_load("21gr")
    db.calls.clear()
    store.save()
    assert not [c for c in db.calls if c[0] == "DELETE"]
    assert ("POST", "rifles") not in db.calls and ("POST", "loads") not in db.calls
    assert _ids(db) == before
    reloaded = FakeStore(db)
    assert reloaded.active_rifle_name == "AR-15"
    assert reloaded.rifles["AR-15"].active_load_name == "21gr"


def test_rename_updates_the_same_row(db):
    before_rifles, before_loads = _ids(db)
    store = FakeStore(db)
    store.update_rifle_fields("AR-15", name="AR-15 20in")
    store.update_load_fields("AR-15 20in", "21gr", name="21.0gr H335", muzzle_velocity_fps=2422)
    store.save()
    rifles, loads = _ids(db)
    assert rifles["AR-15 20in"] == before_rifles["AR-15"]
    assert loads["21.0gr H335"] == before_loads["21gr"]
    assert not [c for c in db.calls if c[0] == "DELETE"]
    assert FakeStore(db).rifles["AR-15 20in"].loads["21.0gr H335"].muzzle_velocity_fps == 2422


def test_deleting_one_load_or_rifle_touches_only_that_row(db):
    before_rifles, before_loads = _ids(db)
    store = FakeStore(db)
    store.delete_load("AR-15", "23gr")
    store.delete_rifle("Bolt gun")
    db.calls.clear()
    store.save()
    assert sorted(c for c in db.calls if c[0] == "DELETE") == [("DELETE", "loads"), ("DELETE", "rifles")]
    rifles, loads = _ids(db)
    assert rifles == {"AR-15": before_rifles["AR-15"]}
    assert loads == {"21gr": before_loads["21gr"]}
    reloaded = FakeStore(db)
    assert reloaded.active_rifle_name == "AR-15"
    assert reloaded.rifles["AR-15"].active_load_name == "21gr"


def test_adding_a_load_inserts_only_that_load(db):
    before_rifles, before_loads = _ids(db)
    store = FakeStore(db)
    store.rifles["Bolt gun"].add_load(_load("168gr"))
    db.calls.clear()
    store.save()
    assert ("POST", "loads") in db.calls
    assert not [c for c in db.calls if c[0] == "DELETE" or c == ("POST", "rifles")]
    rifles, loads = _ids(db)
    assert rifles == before_rifles
    assert {k: v for k, v in loads.items() if k != "168gr"} == before_loads
    assert FakeStore(db).rifles["Bolt gun"].active_load_name == "168gr"


def test_a_replacement_object_with_the_same_name_updates_in_place(db):
    before_rifles, before_loads = _ids(db)
    store = FakeStore(db)
    replacement = Rifle(name="AR-15", scope_height_in=2.6)
    replacement.add_load(_load("21gr", mv=2500.0))
    store.rifles["AR-15"] = replacement
    store.save()
    rifles, loads = _ids(db)
    assert rifles["AR-15"] == before_rifles["AR-15"]
    assert loads["21gr"] == before_loads["21gr"]
    assert "23gr" not in loads
    assert FakeStore(db).rifles["AR-15"].scope_height_in == 2.6


def test_saving_twice_in_one_request_does_not_duplicate(db):
    store = FakeStore(db)
    store.add_rifle(Rifle(name="New one", scope_height_in=2.0))
    store.save()
    store.rifles["New one"].add_load(_load("first"))
    store.save()
    store.save()
    assert sorted(r["name"] for r in db.tables["rifles"]) == ["AR-15", "Bolt gun", "New one"]
    assert [l["name"] for l in db.tables["loads"]].count("first") == 1


def test_a_failed_write_leaves_everything_else_in_the_database(db):
    """The old save() deleted every rifle first, so a failure on the
    re-insert left the account empty."""
    store = FakeStore(db)
    store.rifles["Bolt gun"].add_load(_load("168gr"))
    db.fail_on = ("POST", "loads")
    with pytest.raises(RuntimeError):
        store.save()
    db.fail_on = None
    reloaded = FakeStore(db)
    assert set(reloaded.rifles) == {"AR-15", "Bolt gun"}
    assert set(reloaded.rifles["AR-15"].loads) == {"21gr", "23gr"}


# ---------------------------------------------------------------- solver

def _solve_with_deadline(solver, zero_yd, range_yd, seconds=60):
    box = {}

    def work():
        try:
            box["point"] = solver.at_range(zero_yd, range_yd)
        except Exception as exc:  # noqa: BLE001 -- reported to the test below
            box["error"] = exc

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), "solver did not return"
    return box


def test_unreachable_zero_raises_instead_of_hanging():
    solver = TrajectorySolver(muzzle_velocity_fps=1050, bc=0.12, drag_model="G1", scope_height_in=2.5)
    box = _solve_with_deadline(solver, 2000, 100)
    assert isinstance(box.get("error"), ValueError)
    assert "beyond this load's reach" in str(box["error"])


@pytest.mark.parametrize("zero_yd", [0, -50])
def test_non_positive_zero_raises_instead_of_hanging(zero_yd):
    solver = TrajectorySolver(muzzle_velocity_fps=2766, bc=0.362, drag_model="G1", scope_height_in=2.5)
    box = _solve_with_deadline(solver, zero_yd, 100)
    assert isinstance(box.get("error"), ValueError)


def test_a_long_but_reachable_zero_still_solves():
    """Needs more than the initial 3 degree bracket, so it exercises the
    bounded widening rather than the refusal."""
    solver = TrajectorySolver(muzzle_velocity_fps=1050, bc=0.12, drag_model="G1", scope_height_in=2.5)
    box = _solve_with_deadline(solver, 500, 500)
    assert "error" not in box
    assert abs(box["point"].drop_in) < 0.5


def test_ordinary_zero_is_unchanged():
    solver = TrajectorySolver(muzzle_velocity_fps=2766, bc=0.362, drag_model="G1", scope_height_in=2.5)
    assert abs(solver.at_range(36, 36).drop_in) < 0.05
