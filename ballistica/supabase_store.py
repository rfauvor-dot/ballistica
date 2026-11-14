"""Supabase-backed ProfileStore -- the per-user data layer for
multi-tenancy (MULTI_TENANCY_DESIGN.md).

Deliberately a thin subclass, not a rewrite: Rifle/Load and all of
ProfileStore's fuzzy-matching, validation, and CRUD logic (find_rifle,
delete_rifle, update_rifle_fields, etc.) are already correct and already
tested against the flat-file store -- reused here completely unchanged.
Only the two methods that actually touch storage (load/save) are
overridden to talk to Supabase Postgres instead of a JSON file. Every
request is made with the AUTHENTICATED USER'S OWN access token, never a
service-role key -- that's what makes row-level security the real
isolation boundary (per MULTI_TENANCY_DESIGN.md #7.2's central point:
auth.uid() in RLS policies holds even if this Python code has a bug),
not just something this code promises to filter correctly on its own.

Storage mapping:
- rifles/loads: real tables, one row each, user_id-scoped by RLS.
- active_rifle_name: NOT a rifles-table column (there isn't one) --
  stored in conversation_state.state_json, since "which rifle is
  currently selected" is per-user session preference, not identity
  data, and that table already exists for exactly this kind of state
  (voice conversation state lands there too, in a later phase).

save() writes only what changed since load() (or the previous save()):
it deletes the rows that are gone, updates the rows whose fields differ,
and inserts the new ones, keeping every surviving row's id. It used to
delete ALL of the user's rifles and re-insert them on every call, even
for a plain "switch to X" -- a failure between the delete and the
re-insert (timeout, deploy mid-request) lost everything, two overlapping
requests could overwrite each other with a stale copy, and no row id
survived a save. This is still several separate requests, not one
database transaction, so a failure part-way can leave a save partly
applied -- but there is no longer any moment at which rows that weren't
being changed have been deleted.
"""
from __future__ import annotations

import os

import httpx

from .profiles import Load, ProfileStore, Rifle

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

_RIFLE_COLUMNS = (
    "name", "scope_height_in", "caliber", "barrel_length_in", "twist_rate",
    "click_value_mrad", "reticle_unit", "optic_type", "scope_make", "scope_model",
    "magnification", "objective_lens_mm", "focal_plane", "reticle_type",
    "dot_size_moa", "has_suppressor", "suppressor_type",
)
_LOAD_COLUMNS = (
    "name", "bullet_weight_gr", "bc", "drag_model", "muzzle_velocity_fps",
    "zero_distance_yd", "bullet_type", "powder", "powder_charge_gr", "notes",
)


def _columns(obj, names: tuple[str, ...]) -> dict:
    return {k: getattr(obj, k) for k in names}


class SupabaseProfileStore(ProfileStore):
    def __init__(self, user_id: str, access_token: str) -> None:
        self.user_id = user_id
        self.access_token = access_token
        self.rifles: dict[str, Rifle] = {}
        self.active_rifle_name: str | None = None
        self.load()

    def _headers(self) -> dict:
        return {
            "apikey": _SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _rest(self, method: str, table: str, headers: dict | None = None, **kwargs) -> httpx.Response:
        """headers, if given, are merged on top of the base auth headers
        (e.g. to add Prefer: return=representation) rather than
        replacing them -- a caller passing headers used to collide with
        this method's own headers=self._headers(), a real bug caught
        live: any call site overriding Prefer (every insert/upsert in
        save()) crashed with a duplicate-keyword TypeError before ever
        reaching the network."""
        url = f"{_SUPABASE_URL}/rest/v1/{table}"
        merged_headers = {**self._headers(), **(headers or {})}
        resp = httpx.request(method, url, headers=merged_headers, timeout=15, **kwargs)
        resp.raise_for_status()
        return resp

    def load(self) -> None:
        rifle_rows = self._rest(
            "GET", "rifles", params={"user_id": f"eq.{self.user_id}", "select": "*"},
        ).json()
        load_rows = self._rest(
            "GET", "loads", params={"user_id": f"eq.{self.user_id}", "select": "*"},
        ).json()

        # What the database holds, as of this load()/the last save() --
        # save() diffs the in-memory objects against these. Keyed by row id.
        self._saved_rifles: dict[str, dict] = {}   # id -> {"obj", "cols", "active_load_id"}
        self._saved_loads: dict[str, dict] = {}    # id -> {"obj", "cols", "rifle_id"}

        loads_by_rifle_id: dict[str, dict[str, Load]] = {}
        load_id_to_name: dict[str, str] = {}
        for row in load_rows:
            load_obj = Load(**{k: row[k] for k in _LOAD_COLUMNS})
            loads_by_rifle_id.setdefault(row["rifle_id"], {})[load_obj.name] = load_obj
            load_id_to_name[row["id"]] = load_obj.name
            self._saved_loads[row["id"]] = {
                "obj": load_obj, "cols": _columns(load_obj, _LOAD_COLUMNS), "rifle_id": row["rifle_id"],
            }

        self.rifles = {}
        for row in rifle_rows:
            rifle_loads = loads_by_rifle_id.get(row["id"], {})
            active_load_name = load_id_to_name.get(row["active_load_id"])
            rifle_obj = Rifle(
                **{k: row[k] for k in _RIFLE_COLUMNS},
                loads=rifle_loads,
                active_load_name=active_load_name,
            )
            self.rifles[rifle_obj.name] = rifle_obj
            self._saved_rifles[row["id"]] = {
                "obj": rifle_obj, "cols": _columns(rifle_obj, _RIFLE_COLUMNS),
                "active_load_id": row["active_load_id"],
            }

        state = self.get_conversation_state()
        active_rifle_id = state.get("active_rifle_id")
        self._saved_active_rifle_id = active_rifle_id
        self.active_rifle_name = None
        if active_rifle_id:
            for row in rifle_rows:
                if row["id"] == active_rifle_id:
                    self.active_rifle_name = row["name"]
                    break
        if self.active_rifle_name is None and self.rifles:
            # No stored preference (or it pointed at a rifle that's gone)
            # -- fall back to any rifle, matching add_rifle()'s own
            # "make active if nothing else is" default.
            self.active_rifle_name = next(iter(self.rifles))

    @staticmethod
    def _match_saved(objects: list, saved: dict[str, dict]) -> tuple[dict[int, str], dict[str, dict]]:
        """Pairs in-memory objects with saved rows: by identity first (so a
        rename is an UPDATE of the same row), then by name (so an object
        replaced by a new one of the same name is also an UPDATE, not a
        delete plus an insert). Returns ({id(obj): row id}, the saved rows
        nothing claimed -- i.e. the ones to delete)."""
        unclaimed = dict(saved)
        ids: dict[int, str] = {}
        for same in (lambda entry, obj: entry["obj"] is obj,
                     lambda entry, obj: entry["cols"]["name"] == obj.name):
            for obj in objects:
                if id(obj) in ids:
                    continue
                row_id = next((i for i, entry in unclaimed.items() if same(entry, obj)), None)
                if row_id is not None:
                    ids[id(obj)] = row_id
                    del unclaimed[row_id]
        return ids, unclaimed

    def save(self) -> None:
        """Writes only the difference between the in-memory rifles/loads
        and what load() (or the previous save()) saw -- see the module
        docstring for why this is no longer delete-everything-and-reinsert.
        Order is deletes, then updates, then inserts, so a name freed by a
        delete or a rename is free before anything new claims it."""
        own = {"user_id": f"eq.{self.user_id}"}
        rifles = list(self.rifles.values())
        rifle_ids, gone = self._match_saved(rifles, self._saved_rifles)

        # 1. Rifles that are gone. Their loads go with them (ON DELETE CASCADE).
        for rid in gone:
            self._rest("DELETE", "rifles", params={"id": f"eq.{rid}", **own})
            del self._saved_rifles[rid]
            for lid in [i for i, e in self._saved_loads.items() if e["rifle_id"] == rid]:
                del self._saved_loads[lid]

        # 2. Existing rifles whose own fields changed.
        for rifle in rifles:
            rid = rifle_ids.get(id(rifle))
            if rid is None:
                continue
            cols = _columns(rifle, _RIFLE_COLUMNS)
            entry = self._saved_rifles[rid]
            if cols != entry["cols"]:
                self._rest("PATCH", "rifles", params={"id": f"eq.{rid}", **own}, json=cols)
            entry["obj"], entry["cols"] = rifle, cols

        # 3. New rifles.
        for rifle in rifles:
            if id(rifle) in rifle_ids:
                continue
            cols = _columns(rifle, _RIFLE_COLUMNS)
            row = self._rest(
                "POST", "rifles", json={**cols, "user_id": self.user_id},
                headers={"Prefer": "return=representation"},
            ).json()[0]
            rifle_ids[id(rifle)] = row["id"]
            self._saved_rifles[row["id"]] = {"obj": rifle, "cols": cols, "active_load_id": None}

        # 4. Loads, one rifle at a time, same three steps.
        for rifle in rifles:
            rid = rifle_ids[id(rifle)]
            loads = list(rifle.loads.values())
            load_ids, gone_loads = self._match_saved(
                loads, {i: e for i, e in self._saved_loads.items() if e["rifle_id"] == rid},
            )
            for lid in gone_loads:
                self._rest("DELETE", "loads", params={"id": f"eq.{lid}", **own})
                del self._saved_loads[lid]
                if self._saved_rifles[rid]["active_load_id"] == lid:
                    self._saved_rifles[rid]["active_load_id"] = None  # the FK is ON DELETE SET NULL

            new_loads = []
            for load_obj in loads:
                cols = _columns(load_obj, _LOAD_COLUMNS)
                lid = load_ids.get(id(load_obj))
                if lid is None:
                    new_loads.append((load_obj, cols))
                    continue
                entry = self._saved_loads[lid]
                if cols != entry["cols"]:
                    self._rest("PATCH", "loads", params={"id": f"eq.{lid}", **own}, json=cols)
                entry["obj"], entry["cols"] = load_obj, cols

            if new_loads:
                inserted = self._rest(
                    "POST", "loads",
                    json=[{**cols, "rifle_id": rid, "user_id": self.user_id} for _, cols in new_loads],
                    headers={"Prefer": "return=representation"},
                ).json()
                id_by_name = {row["name"]: row["id"] for row in inserted}
                for load_obj, cols in new_loads:
                    lid = id_by_name[load_obj.name]
                    load_ids[id(load_obj)] = lid
                    self._saved_loads[lid] = {"obj": load_obj, "cols": cols, "rifle_id": rid}

            active_load = rifle.loads.get(rifle.active_load_name) if rifle.active_load_name else None
            active_load_id = load_ids.get(id(active_load)) if active_load is not None else None
            if active_load_id != self._saved_rifles[rid]["active_load_id"]:
                self._rest(
                    "PATCH", "rifles", params={"id": f"eq.{rid}", **own},
                    json={"active_load_id": active_load_id},
                )
                self._saved_rifles[rid]["active_load_id"] = active_load_id

        # 5. Which rifle is active -- only written when it actually changed.
        active_rifle = self.rifles.get(self.active_rifle_name) if self.active_rifle_name else None
        active_rifle_id = rifle_ids.get(id(active_rifle)) if active_rifle is not None else None
        if active_rifle_id != self._saved_active_rifle_id:
            self.set_conversation_state(active_rifle_id=active_rifle_id)
            self._saved_active_rifle_id = active_rifle_id

    def get_conversation_state(self) -> dict:
        """Public: also used directly by api.py's /v2/voice/query to
        hydrate the per-user setup/calibration/pending_delete state on
        each request (cli.py's _SetupSession/_CalibrationSession
        to_dict()/from_dict() round-trip through the same "setup"/
        "calibration"/"pending_delete" keys this dict holds alongside
        "active_rifle_id")."""
        rows = self._rest(
            "GET", "conversation_state",
            params={"user_id": f"eq.{self.user_id}", "select": "state_json"},
        ).json()
        return rows[0]["state_json"] if rows else {}

    def set_conversation_state(self, **updates) -> None:
        # Read-modify-write rather than a blind overwrite -- state_json
        # holds both active_rifle_id (this class's own concern) and
        # voice conversation state (api.py's concern) side by side, and
        # a caller updating one must never clobber the other.
        state = self.get_conversation_state()
        state.update({k: v for k, v in updates.items()})
        self._rest(
            "POST", "conversation_state",
            json={"user_id": self.user_id, "state_json": state},
            headers={"Prefer": "resolution=merge-duplicates"},
        )

    def log_conversation_turn(
        self, input_text: str, tool_name: str, reply_text: str, duration_ms: int | None = None,
    ) -> None:
        """Temporary per-turn debug log (2026-09-06, current build phase
        only -- see db/010_conversation_debug_log.sql). An append-only
        row per voice turn, RLS-scoped the same way as everything else
        in this class (the user's own access token, never service-role),
        so it carries the same real isolation guarantee, not just an
        application-level filter.

        duration_ms added 2026-09-18, per Rick's own "any way we can
        speed up the responses" ask -- reviewing this log to answer that
        question turned up nothing, because nothing before this recorded
        how long a turn actually took, only what was said. Optional and
        appended only when present (db/011_conversation_debug_log_
        duration.sql, same manual-migration constraint as every table
        here) so this call doesn't start failing against a project that
        hasn't run that migration yet -- same reasoning as every other
        best-effort piece of this log."""
        payload = {
            "user_id": self.user_id, "input_text": input_text,
            "tool_name": tool_name, "reply_text": reply_text,
        }
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        self._rest("POST", "conversation_debug_log", json=payload)

    def get_recent_conversation_log(self, limit: int = 50) -> list[dict]:
        """Most recent rows first -- for pulling up what actually
        happened in a real session after the fact."""
        return self._rest(
            "GET", "conversation_debug_log",
            params={
                "user_id": f"eq.{self.user_id}", "select": "*",
                "order": "created_at.desc", "limit": str(limit),
            },
        ).json()

    def create_video_log(self, entry: dict) -> dict:
        """Rick's own idea (2026-09-29, see db/013_video_logs.sql): the
        video itself stays on the shooter's phone -- this saves only the
        small TEXT record (rifle, load, conditions, a label) tying a
        video the shooter will replay locally to what was actually true
        that day, so review means reviewing the right data. Returns the
        inserted row (with its new id) so the caller can display it
        immediately without a second round trip."""
        payload = {**entry, "user_id": self.user_id}
        resp = self._rest(
            "POST", "video_logs", json=payload,
            headers={"Prefer": "return=representation"},
        )
        rows = resp.json()
        return rows[0] if rows else payload

    def list_video_logs(self, limit: int = 200) -> list[dict]:
        """Most recent first -- for picking the entry that matches
        whichever video file the shooter is about to play back."""
        return self._rest(
            "GET", "video_logs",
            params={
                "user_id": f"eq.{self.user_id}", "select": "*",
                "order": "session_date.desc,created_at.desc", "limit": str(limit),
            },
        ).json()

    def delete_video_log(self, entry_id: int) -> None:
        self._rest(
            "DELETE", "video_logs",
            params={"user_id": f"eq.{self.user_id}", "id": f"eq.{entry_id}"},
        )
