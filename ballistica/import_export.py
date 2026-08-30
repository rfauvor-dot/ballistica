"""Bulk CSV/XLSX import and export of rifle/load data (2026-08-30, per
Rick's instruction) -- the backlogged "spreadsheet/CSV data import"
item from BACKLOG.md, now unblocked by multi-tenancy and built with the
scope Rick actually asked for: a column-mapping step (so an arbitrary
spreadsheet's headers don't have to match Ballistica's field names),
paired with an export in the same shape.

Deliberately per-row validation, not all-or-nothing: a file with some
malformed or incomplete rows (e.g., missing a ballistic coefficient --
a real, expected case, not a hypothetical, see the BC field's comment
below) still imports every row that IS valid, and reports exactly why
each failure failed rather than silently skipping or rejecting the
whole file. This is the same "raw observation -> validation, not raw
observation -> straight into storage" principle flagged by an external
review of the (much smaller, still-hypothetical) future aggregate-data
pipeline -- applies just as much here, to data a real user is
importing right now.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import openpyxl

from .profiles import Load, Rifle

MAX_IMPORT_FILE_BYTES = 2 * 1024 * 1024  # 2MB -- real rifle/load data is tiny; a legitimate
                                          # file for this purpose is never anywhere close to this.
MAX_IMPORT_ROWS = 500


@dataclass(frozen=True)
class TargetField:
    key: str
    label: str
    required: bool
    aliases: tuple[str, ...]


# Ordered rifle fields first, then load fields -- also the export column
# order, so an export and a re-import of that same export round-trip
# without any remapping needed. `required` here means "a row can't be
# processed at all without this," not "must be mapped" -- an unmapped
# required field just means every row fails with a clear reason,
# discovered at commit time, not blocked at the mapping step itself.
TARGET_FIELDS: tuple[TargetField, ...] = (
    TargetField("rifle_name", "Rifle Name", True, ("rifle", "rifle name", "firearm", "gun", "gun name")),
    TargetField("caliber", "Caliber", False, ("caliber", "chambering", "cartridge", "chamber")),
    TargetField("barrel_length_in", "Barrel Length (in)", False, ("barrel length", "barrel", "barrel in", "barrel_in", "bbl length")),
    TargetField("twist_rate", "Twist Rate", False, ("twist", "twist rate", "rifling twist")),
    TargetField("scope_height_in", "Scope Height (in)", False, ("scope height", "optic height", "sight height")),
    TargetField("click_value_mrad", "Click Value (MRAD)", False, ("click value", "click", "clicks", "mrad click", "click value mrad")),
    TargetField("reticle_unit", "Reticle Unit (MRAD/MOA)", False, ("reticle unit", "turret unit", "unit")),
    TargetField("optic_type", "Optic Type (scope/red_dot)", False, ("optic type", "optic")),
    TargetField("scope_make", "Scope Make", False, ("scope make", "optic make", "make")),
    # Deliberately no bare "model" alias -- too likely to collide with
    # an unrelated "* Model" header (e.g. "Drag Model") via the loose
    # word-level match pass; "scope model"/"optic model" still match a
    # real scope-model column exactly.
    TargetField("scope_model", "Scope Model", False, ("scope model", "optic model")),
    TargetField("magnification", "Magnification", False, ("magnification", "mag")),
    TargetField("objective_lens_mm", "Objective Lens (mm)", False, ("objective", "objective lens", "objective mm")),
    TargetField("focal_plane", "Focal Plane (FFP/SFP)", False, ("focal plane", "ffp/sfp")),
    TargetField("reticle_type", "Reticle Type", False, ("reticle", "reticle type")),
    TargetField("dot_size_moa", "Dot Size (MOA)", False, ("dot size", "dot size moa")),
    TargetField("has_suppressor", "Suppressed (yes/no)", False, ("suppressed", "suppressor", "has suppressor")),
    TargetField("suppressor_type", "Suppressor Type", False, ("suppressor type", "can")),
    # NOT hard-required despite identifying the load, unlike rifle_name --
    # a real shooter's own load-development log very often has no
    # distinct "load name" column at all (charge weight + powder IS how
    # they think of it, e.g. "22.5gr H335", not a separate label).
    # apply_mapping() synthesizes a reasonable name from powder_charge_gr
    # + powder (or bullet_weight_gr + bullet_type) when this isn't mapped,
    # rather than failing every row over a field most files won't have.
    TargetField("load_name", "Load Name", False, ("load", "load name")),
    TargetField("bullet_type", "Bullet Type", False, ("bullet", "bullet type", "projectile")),
    TargetField("bullet_weight_gr", "Bullet Weight (gr)", False, ("bullet weight", "weight", "grains", "gr", "bullet weight gr")),
    # Required per-row -- the solver hard-requires bc > 0 (see profiles.py's
    # Load.__post_init__) -- there is no meaningful default. A real
    # shooter's own load-development log frequently won't have this
    # (it's a published-data lookup, not something a chronograph
    # measures), so rows missing it are EXPECTED to fail import with a
    # clear reason, not a bug in the importer.
    TargetField("bc", "Ballistic Coefficient", True, ("bc", "ballistic coefficient", "b.c.")),
    TargetField("drag_model", "Drag Model (G1/G7)", False, ("drag model", "model", "g1/g7", "drag")),
    TargetField("muzzle_velocity_fps", "Muzzle Velocity (fps)", True,
                ("velocity", "muzzle velocity", "avg velocity", "average velocity", "fps", "mv")),
    TargetField("zero_distance_yd", "Zero Distance (yd)", False, ("zero", "zero distance", "zero yards", "zero yd")),
    TargetField("powder", "Powder", False, ("powder", "propellant")),
    TargetField("powder_charge_gr", "Powder Charge (gr)", False, ("charge", "powder charge", "charge weight", "charge gr")),
    TargetField("notes", "Notes", False, ("notes", "comment", "comments", "remarks")),
)

_FIELDS_BY_KEY = {f.key: f for f in TARGET_FIELDS}


def _normalize(s: str) -> str:
    return " ".join(s.strip().lower().replace("_", " ").replace("-", " ").split())


class ImportError_(Exception):
    """Raised for a whole-file problem (bad format, too large, no
    rows) -- distinct from a single row failing validation, which is
    reported per-row instead of raising."""


def parse_uploaded_file(filename: str, raw_bytes: bytes) -> tuple[list[str], list[dict[str, str]]]:
    """Returns (headers, rows) -- rows are dicts keyed by the file's own
    original header text, values as strings (numbers included -- kept
    as text here deliberately; numeric parsing happens per-target-field
    during validation, where a bad value can be attributed to a specific
    field and row instead of failing the whole parse)."""
    if len(raw_bytes) > MAX_IMPORT_FILE_BYTES:
        raise ImportError_(f"File is too large (max {MAX_IMPORT_FILE_BYTES // 1024}KB).")

    name_lower = filename.lower()
    if name_lower.endswith(".csv"):
        text = raw_bytes.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        headers = [h for h in (reader.fieldnames or []) if h]
        rows = [
            {k: (v if v is not None else "") for k, v in row.items() if k}
            for row in reader
        ]
    elif name_lower.endswith(".xlsx"):
        try:
            wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
        except Exception as exc:
            raise ImportError_(f"Couldn't read this as an Excel file: {exc}") from exc
        sheet = wb.worksheets[0]
        rows_iter = sheet.iter_rows(values_only=True)
        try:
            header_row = next(rows_iter)
        except StopIteration:
            raise ImportError_("The file has no rows.")
        headers = [str(h).strip() if h is not None else "" for h in header_row]
        headers = [h for h in headers if h]
        rows = []
        for raw_row in rows_iter:
            if all(v is None for v in raw_row):
                continue
            row = {}
            for h, v in zip(header_row, raw_row):
                if not h:
                    continue
                row[str(h).strip()] = "" if v is None else str(v)
            rows.append(row)
    else:
        raise ImportError_("Unsupported file type -- upload a .csv or .xlsx file.")

    if not headers:
        raise ImportError_("Couldn't find a header row in this file.")
    if not rows:
        raise ImportError_("The file has a header row but no data rows.")
    if len(rows) > MAX_IMPORT_ROWS:
        raise ImportError_(f"Too many rows ({len(rows)}) -- max {MAX_IMPORT_ROWS} per import.")
    return headers, rows


def suggest_mapping(headers: list[str]) -> dict[str, str | None]:
    """Best-guess target-field -> source-header mapping, for the
    frontend's mapping screen to pre-fill (the user confirms/corrects
    it before anything is actually imported -- this is a convenience,
    never trusted on its own).

    Two passes across EVERY field, not one pass per field, and each
    matched header is marked claimed so no two fields can grab the
    same one. Found live (not by inspection): a single pass-per-field
    let a short, loose alias on one field (scope_model's old bare
    "model") steal a header that was an EXACT match for a different
    field (drag_model's own "drag model" alias, against a "Drag Model"
    column) just because that field happened to be checked first.
    Resolving every field's exact match first, before any field is
    allowed to fall back to a looser one, closes that whole class of
    bug rather than just this one instance of it."""
    normalized_headers = {h: _normalize(h) for h in headers}
    alias_sets = {
        field.key: {_normalize(a) for a in field.aliases} | {_normalize(field.key)} | {_normalize(field.label)}
        for field in TARGET_FIELDS
    }
    mapping: dict[str, str | None] = {field.key: None for field in TARGET_FIELDS}
    claimed: set[str] = set()

    for field in TARGET_FIELDS:  # pass 1: exact normalized match only
        for h, norm_h in normalized_headers.items():
            if h in claimed:
                continue
            if norm_h in alias_sets[field.key]:
                mapping[field.key] = h
                claimed.add(h)
                break

    for field in TARGET_FIELDS:  # pass 2: looser word-level match, remaining fields/headers only
        if mapping[field.key] is not None:
            continue
        for h, norm_h in normalized_headers.items():
            if h in claimed:
                continue
            if any(a in norm_h.split() for a in alias_sets[field.key]):
                mapping[field.key] = h
                claimed.add(h)
                break

    return mapping


def _to_float(raw: str) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_bool(raw: str) -> bool:
    return str(raw).strip().lower() in ("yes", "y", "true", "1")


@dataclass
class RowResult:
    row_number: int  # 1-indexed, matches the file's own data rows (header excluded)
    rifle_name: str | None
    load_name: str | None
    status: str  # "created" | "updated" | "failed"
    detail: str


def _extract(row: dict[str, str], mapping: dict[str, str | None], key: str) -> str:
    source_header = mapping.get(key)
    if not source_header:
        return ""
    return (row.get(source_header) or "").strip()


def apply_mapping(
    rows: list[dict[str, str]], mapping: dict[str, str | None], existing_rifles: dict[str, Rifle],
) -> tuple[list[RowResult], dict[str, Rifle]]:
    """Builds/updates Rifle and Load objects from the mapped rows.
    Existing rifles (already loaded from this user's real data) are
    reused and only ever gain a load -- an import never overwrites a
    rifle's own metadata, only adds to it, so re-importing a partial
    export can't clobber fields that were filled in some other way
    since. Returns (per-row results, the full set of rifles touched --
    both newly created and updated -- for the caller to persist)."""
    results: list[RowResult] = []
    touched: dict[str, Rifle] = {}

    for i, row in enumerate(rows, start=1):
        rifle_name = _extract(row, mapping, "rifle_name")
        if not rifle_name:
            results.append(RowResult(i, None, _extract(row, mapping, "load_name") or None, "failed", "Missing rifle name."))
            continue

        powder = _extract(row, mapping, "powder")
        powder_charge_gr = _to_float(_extract(row, mapping, "powder_charge_gr"))
        bullet_weight_gr_raw = _to_float(_extract(row, mapping, "bullet_weight_gr"))
        bullet_type = _extract(row, mapping, "bullet_type")

        load_name = _extract(row, mapping, "load_name")
        if not load_name:
            if powder_charge_gr is not None and powder:
                load_name = f"{powder_charge_gr:g}gr {powder}"
            elif bullet_weight_gr_raw is not None and bullet_type:
                load_name = f"{bullet_weight_gr_raw:g}gr {bullet_type}"
            else:
                load_name = f"Load {i}"

        bc = _to_float(_extract(row, mapping, "bc"))
        muzzle_velocity_fps = _to_float(_extract(row, mapping, "muzzle_velocity_fps"))
        if bc is None or bc <= 0:
            results.append(RowResult(
                i, rifle_name, load_name, "failed",
                "Ballistic coefficient is missing or not a positive number -- Ballistica can't "
                "compute a solution without it. Add it (check the bullet manufacturer's published "
                "data) and re-import this row.",
            ))
            continue
        if muzzle_velocity_fps is None or muzzle_velocity_fps <= 0:
            results.append(RowResult(
                i, rifle_name, load_name, "failed", "Muzzle velocity is missing or not a positive number.",
            ))
            continue

        drag_model = (_extract(row, mapping, "drag_model") or "G1").upper()
        if drag_model not in ("G1", "G7"):
            results.append(RowResult(
                i, rifle_name, load_name, "failed", f"Drag model must be G1 or G7, got '{drag_model}'.",
            ))
            continue

        zero_distance_yd = _to_float(_extract(row, mapping, "zero_distance_yd")) or 100.0
        bullet_weight_gr = bullet_weight_gr_raw or 0.0

        rifle = touched.get(rifle_name) or existing_rifles.get(rifle_name)
        rifle_is_new = rifle is None
        if rifle_is_new:
            scope_height_in = _to_float(_extract(row, mapping, "scope_height_in"))
            try:
                rifle = Rifle(
                    name=rifle_name,
                    scope_height_in=scope_height_in if scope_height_in is not None else 0.0,
                    caliber=_extract(row, mapping, "caliber"),
                    barrel_length_in=_to_float(_extract(row, mapping, "barrel_length_in")),
                    twist_rate=_extract(row, mapping, "twist_rate"),
                    click_value_mrad=_to_float(_extract(row, mapping, "click_value_mrad")) or 0.1,
                    reticle_unit=(_extract(row, mapping, "reticle_unit") or "MRAD").upper(),
                    optic_type=_extract(row, mapping, "optic_type"),
                    scope_make=_extract(row, mapping, "scope_make"),
                    scope_model=_extract(row, mapping, "scope_model"),
                    magnification=_extract(row, mapping, "magnification"),
                    objective_lens_mm=_to_float(_extract(row, mapping, "objective_lens_mm")),
                    focal_plane=_extract(row, mapping, "focal_plane"),
                    reticle_type=_extract(row, mapping, "reticle_type"),
                    dot_size_moa=_to_float(_extract(row, mapping, "dot_size_moa")),
                    has_suppressor=_to_bool(_extract(row, mapping, "has_suppressor")),
                    suppressor_type=_extract(row, mapping, "suppressor_type"),
                )
            except ValueError as exc:
                results.append(RowResult(i, rifle_name, load_name, "failed", f"Rifle data invalid: {exc}"))
                continue

        try:
            load = Load(
                name=load_name,
                bullet_weight_gr=bullet_weight_gr, bc=bc, drag_model=drag_model,
                muzzle_velocity_fps=muzzle_velocity_fps, zero_distance_yd=zero_distance_yd,
                bullet_type=bullet_type,
                powder=powder,
                powder_charge_gr=powder_charge_gr,
                notes=_extract(row, mapping, "notes"),
            )
        except ValueError as exc:
            results.append(RowResult(i, rifle_name, load_name, "failed", f"Load data invalid: {exc}"))
            continue

        load_is_new = load_name not in rifle.loads
        rifle.add_load(load, make_active=load_is_new and not rifle.loads)
        touched[rifle_name] = rifle

        detail = "Created." if rifle_is_new else ("Added load to existing rifle." if load_is_new else "Updated existing load.")
        if rifle_is_new and scope_height_in is None:
            detail += " Scope height wasn't provided -- defaulted to 0; add it before relying on solutions from this rifle."
        results.append(RowResult(i, rifle_name, load_name, "created" if rifle_is_new or load_is_new else "updated", detail))

    return results, touched


# ------------------------------------------------------------------ export

_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value) -> str:
    """Mitigates CSV/formula injection (OWASP): a cell whose text
    starts with a formula-trigger character gets a leading apostrophe,
    which Excel/Sheets treat as "force text" rather than evaluating it
    as a formula. Necessary because every field here is free text a
    user fully controls (rifle name, notes, ...) and this file is
    explicitly meant to be opened in a spreadsheet app and potentially
    shared with someone else."""
    if value is None:
        return ""
    s = str(value)
    if s and s[0] in _FORMULA_TRIGGER_CHARS:
        return "'" + s
    return s


def generate_export_csv(rifles: list[Rifle]) -> bytes:
    """One row per load; a rifle with zero loads still gets one row
    with the load columns blank, so it isn't silently dropped from the
    export. Column order matches TARGET_FIELDS exactly, so this file
    re-imports with an auto-suggested mapping that needs no correction
    at all -- a clean round-trip, which matters both for the
    before-you-delete-your-account use case and for genuinely moving
    data between two Ballistica accounts."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([f.label for f in TARGET_FIELDS])

    def rifle_cells(rifle: Rifle) -> list[str]:
        return [
            _csv_safe(rifle.name), _csv_safe(rifle.caliber), _csv_safe(rifle.barrel_length_in),
            _csv_safe(rifle.twist_rate), _csv_safe(rifle.scope_height_in), _csv_safe(rifle.click_value_mrad),
            _csv_safe(rifle.reticle_unit), _csv_safe(rifle.optic_type), _csv_safe(rifle.scope_make),
            _csv_safe(rifle.scope_model), _csv_safe(rifle.magnification), _csv_safe(rifle.objective_lens_mm),
            _csv_safe(rifle.focal_plane), _csv_safe(rifle.reticle_type), _csv_safe(rifle.dot_size_moa),
            _csv_safe("yes" if rifle.has_suppressor else "no"), _csv_safe(rifle.suppressor_type),
        ]

    def load_cells(load: Load) -> list[str]:
        return [
            _csv_safe(load.name), _csv_safe(load.bullet_type), _csv_safe(load.bullet_weight_gr),
            _csv_safe(load.bc), _csv_safe(load.drag_model), _csv_safe(load.muzzle_velocity_fps),
            _csv_safe(load.zero_distance_yd), _csv_safe(load.powder), _csv_safe(load.powder_charge_gr),
            _csv_safe(load.notes),
        ]

    blank_load = ["", "", "", "", "", "", "", "", "", ""]
    for rifle in rifles:
        if not rifle.loads:
            writer.writerow(rifle_cells(rifle) + blank_load)
        for load in rifle.loads.values():
            writer.writerow(rifle_cells(rifle) + load_cells(load))

    return buf.getvalue().encode("utf-8-sig")  # BOM so Excel opens UTF-8 correctly, not just Sheets/etc.
