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

save() does a full delete-and-reinsert of the user's rifles/loads on
every call, matching the flat-file store's own save() semantics
exactly (a full snapshot overwrite, not an incremental diff) -- correct
and simple at the scale this app actually runs at (one user's handful
of rifles), not a performance concern worth engineering around.
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

        loads_by_rifle_id: dict[str, dict[str, Load]] = {}
        load_id_to_name: dict[str, str] = {}
        for row in load_rows:
            load_obj = Load(**{k: row[k] for k in _LOAD_COLUMNS})
            loads_by_rifle_id.setdefault(row["rifle_id"], {})[load_obj.name] = load_obj
            load_id_to_name[row["id"]] = load_obj.name

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

        state = self.get_conversation_state()
        active_rifle_id = state.get("active_rifle_id")
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

    def save(self) -> None:
        self._rest("DELETE", "rifles", params={"user_id": f"eq.{self.user_id}"})

        if not self.rifles:
            self.set_conversation_state(active_rifle_id=None)
            return

        rifle_payload = [
            {**{k: getattr(rifle, k) for k in _RIFLE_COLUMNS}, "user_id": self.user_id}
            for rifle in self.rifles.values()
        ]
        inserted_rifles = self._rest(
            "POST", "rifles", json=rifle_payload,
            headers={"Prefer": "return=representation"},
        ).json()
        rifle_id_by_name = {row["name"]: row["id"] for row in inserted_rifles}

        load_payload = []
        for rifle in self.rifles.values():
            rifle_id = rifle_id_by_name[rifle.name]
            for load_obj in rifle.loads.values():
                load_payload.append({
                    **{k: getattr(load_obj, k) for k in _LOAD_COLUMNS},
                    "rifle_id": rifle_id, "user_id": self.user_id,
                })
        load_id_by_rifle_and_name = {}
        if load_payload:
            inserted_loads = self._rest(
                "POST", "loads", json=load_payload,
                headers={"Prefer": "return=representation"},
            ).json()
            for row in inserted_loads:
                load_id_by_rifle_and_name[(row["rifle_id"], row["name"])] = row["id"]

        for rifle in self.rifles.values():
            if rifle.active_load_name is None:
                continue
            rifle_id = rifle_id_by_name[rifle.name]
            load_id = load_id_by_rifle_and_name.get((rifle_id, rifle.active_load_name))
            if load_id:
                self._rest(
                    "PATCH", "rifles", params={"id": f"eq.{rifle_id}"},
                    json={"active_load_id": load_id},
                )

        active_rifle_id = rifle_id_by_name.get(self.active_rifle_name) if self.active_rifle_name else None
        self.set_conversation_state(active_rifle_id=active_rifle_id)

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
