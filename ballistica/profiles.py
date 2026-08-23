"""Persistent rifle/load profiles.

Rick runs multiple rifles, each with multiple candidate loads. This
stores that as JSON so it survives between sessions, and supports
switching the active rifle/load by (fuzzy, voice-friendly) name.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re

DEFAULT_PROFILES_PATH = Path(__file__).resolve().parent.parent / "data" / "profiles.json"

_TOKEN_RE = re.compile(r"\d+\.?\d*|[a-z]+")

# Words a real spoken query naturally includes ("switch to MY 21 grain
# LOAD") that never appear in a load/rifle's actual name -- stripped
# from the query side only (never the target side) so they can't cause
# an otherwise-correct fuzzy match to fail just because every query
# token is required to match something.
_QUERY_FILLER_WORDS = {
    "my", "the", "a", "an", "to", "load", "rifle", "gun", "use", "please",
    "switch", "for",
}


def _tokens(text: str) -> list[str]:
    """Loosens matching for voice-transcribed queries: lowercase, fold
    "grain(s)" to "gr", then split into word/number tokens so "23.5
    grain" matches a load named "23.5gr H335" despite the decimal
    point and missing space that a plain substring check would trip
    on."""
    text = text.lower().replace("grains", "gr").replace("grain", "gr")
    return _TOKEN_RE.findall(text)


def _query_tokens(text: str) -> list[str]:
    """Same as _tokens(), plus strips common filler words that a real
    spoken query includes but a load/rifle's actual name never does."""
    return [t for t in _tokens(text) if t not in _QUERY_FILLER_WORDS]


def _tokens_match(query_tokens: list[str], target_tokens: list[str]) -> bool:
    """Every query token must prefix-match some token in the target
    (either direction, so "21" matches "21.0" and "gr" matches "gr")."""
    if not query_tokens:
        return False
    return all(
        any(t.startswith(q) or q.startswith(t) for t in target_tokens)
        for q in query_tokens
    )


@dataclass
class Load:
    name: str
    bullet_weight_gr: float
    bc: float
    drag_model: str  # "G1" or "G7"
    muzzle_velocity_fps: float
    zero_distance_yd: float
    bullet_type: str = ""  # e.g. "77gr Sierra MatchKing" -- descriptive, distinct from name
    powder: str = ""
    powder_charge_gr: float | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if self.drag_model not in ("G1", "G7"):
            raise ValueError("drag_model must be 'G1' or 'G7'")
        if self.bc <= 0 or self.muzzle_velocity_fps <= 0:
            raise ValueError("bc and muzzle_velocity_fps must be positive")


@dataclass
class Rifle:
    name: str
    scope_height_in: float
    caliber: str = ""
    barrel_length_in: float | None = None
    twist_rate: str = ""
    click_value_mrad: float = 0.1
    reticle_unit: str = "MRAD"  # "MRAD" or "MOA" -- which unit the optic's turrets/reticle actually use
    # "" (unknown/legacy profile), "scope" (magnified), or "red_dot" (fixed
    # 1x reflex/holographic) -- added after a real setup failure: a red dot
    # has no magnification or focal plane at all, and the fields below were
    # built assuming every optic is a magnified scope. This determines
    # which of those fields the voice setup interview even asks about.
    optic_type: str = ""
    scope_make: str = ""
    scope_model: str = ""
    magnification: str = ""  # free text, e.g. "5-25x" or a fixed "10x" -- magnified scopes only
    objective_lens_mm: float | None = None
    focal_plane: str = ""  # "FFP", "SFP", or "" if unknown/not applicable -- magnified scopes only
    reticle_type: str = ""  # magnified scope: e.g. "MOA Christmas tree". Red dot: the dot/circle pattern, e.g. "65 MOA circle + 2 MOA dot"
    dot_size_moa: float | None = None  # red dot only -- the dot's apparent size in MOA
    # Tied to the rifle, not any one load (Addendum 36): the same can stays
    # on the host through both supersonic and subsonic ammo. suppressor_type
    # is deliberately open text, not a brand enum -- plenty of real cans are
    # homemade/custom builds with no commercial brand name to pick from.
    has_suppressor: bool = False
    suppressor_type: str = ""
    loads: dict[str, Load] = field(default_factory=dict)
    active_load_name: str | None = None

    def __post_init__(self) -> None:
        if self.reticle_unit not in ("MRAD", "MOA"):
            raise ValueError("reticle_unit must be 'MRAD' or 'MOA'")
        if self.optic_type not in ("", "scope", "red_dot"):
            raise ValueError("optic_type must be 'scope', 'red_dot', or unset")

    def add_load(self, load: Load, make_active: bool = True) -> None:
        self.loads[load.name] = load
        if make_active or self.active_load_name is None:
            self.active_load_name = load.name

    def get_active_load(self) -> Load:
        if self.active_load_name is None or self.active_load_name not in self.loads:
            raise ValueError(f"Rifle '{self.name}' has no active load")
        return self.loads[self.active_load_name]

    def find_load(self, query: str) -> Load:
        """Fuzzy, voice-friendly lookup: exact name, then case-insensitive
        substring match against name/powder/notes."""
        if query in self.loads:
            return self.loads[query]
        q_tokens = _query_tokens(query)
        matches = [
            load for load in self.loads.values()
            if _tokens_match(q_tokens, _tokens(f"{load.name} {load.powder} {load.notes}"))
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise KeyError(f"No load matching '{query}' on rifle '{self.name}'")
        raise KeyError(f"'{query}' matches multiple loads on '{self.name}': "
                        f"{[m.name for m in matches]}")


class ProfileStore:
    def __init__(self, path: Path | str = DEFAULT_PROFILES_PATH) -> None:
        self.path = Path(path)
        self.rifles: dict[str, Rifle] = {}
        self.active_rifle_name: str | None = None
        if self.path.exists():
            self.load()

    def add_rifle(self, rifle: Rifle, make_active: bool = True) -> None:
        self.rifles[rifle.name] = rifle
        if make_active or self.active_rifle_name is None:
            self.active_rifle_name = rifle.name

    def get_active_rifle(self) -> Rifle:
        if self.active_rifle_name is None or self.active_rifle_name not in self.rifles:
            raise ValueError("No active rifle set")
        return self.rifles[self.active_rifle_name]

    def find_rifle(self, query: str) -> Rifle:
        if query in self.rifles:
            return self.rifles[query]
        q_tokens = _query_tokens(query)
        matches = [r for r in self.rifles.values() if _tokens_match(q_tokens, _tokens(r.name))]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise KeyError(f"No rifle matching '{query}'")
        raise KeyError(f"'{query}' matches multiple rifles: {[m.name for m in matches]}")

    def set_active_rifle(self, query: str) -> Rifle:
        rifle = self.find_rifle(query)
        self.active_rifle_name = rifle.name
        return rifle

    def delete_rifle(self, query: str) -> Rifle:
        """Removes a rifle (and its loads) entirely. There was previously
        no way to do this at all -- confirmed as a real gap (Addendum 29),
        both for cleaning up a bad/duplicate entry and for removing test
        data. If the deleted rifle was active, an arbitrary remaining
        rifle becomes active instead (or none, if it was the last one)."""
        rifle = self.find_rifle(query)
        del self.rifles[rifle.name]
        if self.active_rifle_name == rifle.name:
            self.active_rifle_name = next(iter(self.rifles), None)
        return rifle

    def set_active_load(self, query: str) -> Load:
        """Switches the active load on the active rifle by fuzzy name."""
        rifle = self.get_active_rifle()
        load = rifle.find_load(query)
        rifle.active_load_name = load.name
        return load

    def update_load_velocity(self, rifle_query: str, load_query: str, new_velocity_fps: float) -> Load:
        """Updates just the muzzle velocity on an existing load, e.g.
        after chronograph testing, without re-entering everything else."""
        rifle = self.find_rifle(rifle_query)
        load = rifle.find_load(load_query)
        load.muzzle_velocity_fps = new_velocity_fps
        return load

    def update_rifle_fields(self, rifle_query: str, **fields) -> Rifle:
        """Updates rifle metadata (scope height, caliber, barrel length,
        twist rate, click value, etc.) in place on an existing rifle."""
        rifle = self.find_rifle(rifle_query)
        original = {}
        for key, value in fields.items():
            if not hasattr(rifle, key):
                raise ValueError(f"Rifle has no field '{key}'")
            original[key] = getattr(rifle, key)
            setattr(rifle, key, value)
        try:
            # setattr() bypasses __post_init__'s validation entirely -- a
            # bad reticle_unit or optic_type would previously be written
            # silently, with no error, and only surface later as a wrong
            # or confusing ballistics result. Re-running it here closes
            # that gap.
            rifle.__post_init__()
        except ValueError:
            # setattr() already happened above -- without rolling back,
            # a rejected update would leave the rifle silently corrupted
            # for every subsequent call, not just this one (reproduced
            # directly: a bad reticle_unit stuck around even after this
            # call correctly raised).
            for key, value in original.items():
                setattr(rifle, key, value)
            raise
        return rifle

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "active_rifle_name": self.active_rifle_name,
            "rifles": {name: asdict(rifle) for name, rifle in self.rifles.items()},
        }
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.active_rifle_name = data.get("active_rifle_name")
        self.rifles = {}
        for name, rdata in data.get("rifles", {}).items():
            loads = {lname: Load(**ldata) for lname, ldata in rdata.pop("loads", {}).items()}
            rifle = Rifle(**{**rdata, "loads": loads})
            self.rifles[name] = rifle
