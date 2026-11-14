"""Fixes 3-5 from the 2026-11-14 codebase review:

3. A range the bullet is never tracked to is refused instead of answered
   with the last tracked point; a supersonic load that has gone subsonic by
   the target gets a spoken caution.
4. The page is served with a nonce-based Content-Security-Policy.
5. A delete only goes ahead on a clear, leading yes; "delete that load"
   deletes one load, not the whole rifle.
"""
from __future__ import annotations

import pytest

import ballistica.cli as cli_module
from ballistica.cli import BallisticaCLI, bootstrap_default_profile
from ballistica.profiles import Load, ProfileStore
from ballistica.trajectory import TrajectorySolver


@pytest.fixture
def cli(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("this turn must not reach the LLM")

    for name in ("extract_intent", "classify_calibration_turn", "extract_setup_fields"):
        monkeypatch.setattr(cli_module, name, boom)
    store = ProfileStore(tmp_path / "profiles.json")
    bootstrap_default_profile(store)   # AR-15 20in Faxon; loads 21.0gr H335, 23.5gr H335 (active)
    return BallisticaCLI(store)


# ------------------------------------------------------------- 3. reach

def _smk():
    return TrajectorySolver(muzzle_velocity_fps=2766, bc=0.362, drag_model="G1", scope_height_in=2.5)


def test_range_past_the_bullets_reach_is_refused_not_shortened():
    with pytest.raises(ValueError, match="past where this load can be solved"):
        _smk().at_range(36, 5000)


def test_drop_table_leaves_out_rows_past_reach():
    rows = _smk().drop_table(36, 5000, 1000)
    assert [round(p.range_yd) for p in rows] == [0, 1000, 2000, 3000]


def test_voice_solution_past_reach_says_so(cli):
    with pytest.raises(ValueError, match="past where this load can be solved"):
        cli.handle("drop at 5000 yards")   # api.py speaks a ValueError's text back


def test_supersonic_load_gone_subsonic_gets_a_caution(cli):
    near = cli.handle("drop at 300 yards")
    far = cli.handle("drop at 1400 yards")
    assert "approximate" not in near
    assert far.startswith("Solution, 1400 yards.")
    assert far.endswith("Past supersonic range, so treat that as approximate.")


def test_a_load_that_starts_subsonic_gets_no_caution(cli):
    rifle = cli.store.get_active_rifle()
    rifle.add_load(Load(name="subs", bullet_weight_gr=220, bc=0.6, drag_model="G1",
                        muzzle_velocity_fps=1020, zero_distance_yd=50))
    assert "approximate" not in cli.handle("drop at 200 yards")


# ---------------------------------------------------------------- 4. CSP

def test_page_is_served_with_a_nonce_csp(api_client):
    first, second = api_client.get("/"), api_client.get("/")
    csp = first.headers["content-security-policy"]
    nonce = csp.split("'nonce-")[1].split("'")[0]
    assert "script-src 'self' 'nonce-" in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert "object-src 'none'" in csp and "frame-ancestors 'none'" in csp
    # Every script tag on the page carries this response's nonce.
    assert first.text.count("<script") == first.text.count(f'<script nonce="{nonce}"') == 2
    assert nonce not in second.headers["content-security-policy"]


# ------------------------------------------------------------- 5. delete

@pytest.mark.parametrize("reply", [
    "all right, hold on", "right, wait", "yes -- no wait", "that's not right",
    "is that the right one", "hmm not sure", "no", "correct me if I'm wrong but don't",
])
def test_anything_short_of_a_clear_yes_keeps_the_rifle(cli, reply):
    rifle = cli.store.active_rifle_name
    cli.handle("delete this rifle")
    assert "keeping it" in cli.handle(reply).lower()
    assert rifle in cli.store.rifles and cli._pending_delete is None


@pytest.mark.parametrize("reply", ["yes", "Yeah, delete it", "that is correct", "that's right", "correct", "go ahead"])
def test_a_clear_yes_deletes_the_rifle(cli, reply):
    rifle = cli.store.active_rifle_name
    cli.handle("delete this rifle")
    assert "deleted" in cli.handle(reply).lower()
    assert rifle not in cli.store.rifles


def test_delete_that_load_deletes_only_the_active_load(cli):
    rifle = cli.store.get_active_rifle()
    ask = cli.handle("delete that load")
    assert "23.5gr H335 load" in ask and "all its loads" not in ask
    assert cli._pending_delete == rifle.name and cli._pending_delete_load == "23.5gr H335"
    assert cli.handle("yes") == "Deleted the 23.5gr H335 load."
    assert rifle.name in cli.store.rifles
    assert list(rifle.loads) == ["21.0gr H335"] and rifle.active_load_name == "21.0gr H335"


def test_delete_a_named_load(cli):
    rifle = cli.store.get_active_rifle()
    assert "21.0gr H335 load" in cli.handle("get rid of the 21 grain load")
    cli.handle("yes")
    assert list(rifle.loads) == ["23.5gr H335"]


def test_declining_a_load_delete_keeps_it(cli):
    rifle = cli.store.get_active_rifle()
    cli.handle("delete this load")
    assert "keeping it" in cli.handle("all right, hold on").lower()
    assert len(rifle.loads) == 2 and cli._pending_delete_load is None


def test_delete_this_rifle_still_means_the_rifle(cli):
    assert "and all its loads" in cli.handle("delete this rifle")
    assert cli._pending_delete_load is None


def test_pending_load_delete_survives_the_api_round_trip(cli):
    from ballistica.api import _dehydrate_cli, _hydrate_cli

    cli.handle("delete that load")
    fresh = BallisticaCLI(cli.store)
    _hydrate_cli(fresh, _dehydrate_cli(cli))
    assert fresh._pending_delete_load == "23.5gr H335"
    assert fresh.handle("yes") == "Deleted the 23.5gr H335 load."
