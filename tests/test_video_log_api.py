"""HTTP layer for the video-log feature (2026-09-29). The video file itself
never touches this endpoint -- only a small text record. Auth and the
Supabase-backed store are both overridden (no live Supabase needed); a
separate live smoke test isn't required here since supabase_store.py's
create/list/delete methods are thin, already-covered-by-pattern REST calls
identical in shape to conversation_debug_log's, which IS exercised live
elsewhere."""
import httpx
import pytest
from fastapi.testclient import TestClient

import ballistica.api as api_module


class _FakeVideoLogStore:
    """Stands in for SupabaseProfileStore for exactly the three methods
    this feature calls -- everything else would raise if touched, which is
    deliberate: these tests are scoped to the video-log endpoints only."""
    def __init__(self):
        self.rows: list[dict] = []
        self._next_id = 1
        self.table_missing = False

    def _maybe_raise(self):
        if self.table_missing:
            resp = httpx.Response(404, request=httpx.Request("GET", "https://x/rest/v1/video_logs"))
            raise httpx.HTTPStatusError("relation does not exist", request=resp.request, response=resp)

    def create_video_log(self, entry: dict) -> dict:
        self._maybe_raise()
        row = {**entry, "id": self._next_id, "user_id": "user-1"}
        self._next_id += 1
        self.rows.append(row)
        return row

    def list_video_logs(self, limit: int = 200) -> list[dict]:
        self._maybe_raise()
        return sorted(self.rows, key=lambda r: (r["session_date"], r["id"]), reverse=True)[:limit]

    def delete_video_log(self, entry_id: int) -> None:
        self._maybe_raise()
        self.rows = [r for r in self.rows if r["id"] != entry_id]


@pytest.fixture
def store():
    return _FakeVideoLogStore()


@pytest.fixture
def client(store):
    api_module.app.dependency_overrides[api_module._verify_bearer] = lambda: ("user-1", "token-1")
    api_module.app.dependency_overrides[api_module._get_user_store] = lambda: store
    yield TestClient(api_module.app)
    api_module.app.dependency_overrides.pop(api_module._verify_bearer, None)
    api_module.app.dependency_overrides.pop(api_module._get_user_store, None)


def _entry(**overrides):
    base = {
        "label": "Pistol video 1 -- flinch check", "rifle_name": "Sig P320",
        "load_name": "115gr FMJ", "session_date": "2026-09-27",
        "temp_f": 68.0, "humidity_pct": 30.0, "wind_mph": 5.0, "wind_clock": 3.0,
        "altitude_ft": 2500.0, "pressure_inhg": 29.9,
    }
    base.update(overrides)
    return base


def test_endpoints_require_login():
    client = TestClient(api_module.app)
    assert client.post("/v2/video-log", json=_entry()).status_code in (401, 422)
    assert client.get("/v2/video-log").status_code in (401, 422)
    assert client.delete("/v2/video-log/1").status_code in (401, 422)


def test_create_returns_the_saved_row_with_an_id(client):
    r = client.post("/v2/video-log", json=_entry())
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["id"] == 1 and j["label"] == "Pistol video 1 -- flinch check"
    assert j["rifle_name"] == "Sig P320" and j["session_date"] == "2026-09-27"


def test_the_video_file_itself_is_never_part_of_the_request(client):
    """No file upload field exists on this endpoint at all -- confirms the
    design intent (video stays on the shooter's phone) at the API surface,
    not just in a docstring."""
    r = client.post("/v2/video-log", json=_entry())
    assert r.status_code == 200
    # A multipart file on this JSON-only endpoint would be silently ignored,
    # not stored -- there is no field name in VideoLogIn that could hold one.
    from ballistica.api import VideoLogIn
    assert "video" not in VideoLogIn.model_fields and "file" not in VideoLogIn.model_fields


def test_list_is_most_recent_session_first(client):
    client.post("/v2/video-log", json=_entry(label="oldest", session_date="2026-09-01"))
    client.post("/v2/video-log", json=_entry(label="newest", session_date="2026-09-27"))
    client.post("/v2/video-log", json=_entry(label="middle", session_date="2026-09-15"))
    r = client.get("/v2/video-log")
    labels = [e["label"] for e in r.json()["entries"]]
    assert labels == ["newest", "middle", "oldest"]


def test_delete_removes_only_that_entry(client):
    a = client.post("/v2/video-log", json=_entry(label="keep")).json()
    b = client.post("/v2/video-log", json=_entry(label="remove")).json()
    r = client.delete(f"/v2/video-log/{b['id']}")
    assert r.status_code == 200 and r.json() == {"deleted": True}
    labels = [e["label"] for e in client.get("/v2/video-log").json()["entries"]]
    assert labels == ["keep"]


def test_a_bad_date_is_rejected(client):
    r = client.post("/v2/video-log", json=_entry(session_date="not-a-date"))
    assert r.status_code == 422


@pytest.mark.parametrize("field,bad", [
    ("temp_f", 999), ("humidity_pct", 150), ("wind_mph", -5),
    ("wind_clock", 13), ("pressure_inhg", 5),
])
def test_out_of_range_conditions_are_rejected(client, field, bad):
    r = client.post("/v2/video-log", json=_entry(**{field: bad}))
    assert r.status_code == 422


def test_label_and_rifle_name_are_required(client):
    assert client.post("/v2/video-log", json=_entry(label="")).status_code == 422
    assert client.post("/v2/video-log", json=_entry(rifle_name="")).status_code == 422


def test_conditions_and_load_name_and_notes_are_all_optional(client):
    r = client.post("/v2/video-log", json={
        "label": "Bare minimum", "rifle_name": "AR-15", "session_date": "2026-09-27",
    })
    assert r.status_code == 200, r.text
    assert r.json()["label"] == "Bare minimum"


def test_missing_table_gives_a_clear_setup_instruction_not_a_500(client, store):
    store.table_missing = True
    r = client.post("/v2/video-log", json=_entry())
    assert r.status_code == 503 and "013_video_logs.sql" in r.json()["detail"]
    r2 = client.get("/v2/video-log")
    assert r2.status_code == 503 and "013_video_logs.sql" in r2.json()["detail"]
