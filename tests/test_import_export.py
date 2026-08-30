"""Tests for ballistica/import_export.py -- the bulk CSV/XLSX rifle/load
import and export feature (2026-08-30). No network/Supabase dependency:
this module operates entirely on Rifle/Load dataclasses and raw
file bytes, so these tests run everywhere, unlike test_tenant_isolation.py.
"""
from __future__ import annotations

import io

import openpyxl
import pytest

from ballistica.import_export import (
    ImportError_, MAX_IMPORT_ROWS, apply_mapping, generate_export_csv,
    parse_uploaded_file, suggest_mapping,
)
from ballistica.profiles import Load, Rifle


def _csv_bytes(text: str) -> bytes:
    return text.encode()


# ------------------------------------------------------------- parsing

def test_parses_simple_csv():
    headers, rows = parse_uploaded_file("x.csv", _csv_bytes("A,B\n1,2\n3,4\n"))
    assert headers == ["A", "B"]
    assert rows == [{"A": "1", "B": "2"}, {"A": "3", "B": "4"}]


def test_parses_csv_with_bom():
    headers, rows = parse_uploaded_file("x.csv", "﻿A,B\n1,2\n".encode("utf-8"))
    assert headers == ["A", "B"]  # BOM must not leak into the first header's text


def test_parses_xlsx():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Rifle", "Velocity"])
    ws.append(["Faxon 20in", 2822])
    buf = io.BytesIO()
    wb.save(buf)
    headers, rows = parse_uploaded_file("x.xlsx", buf.getvalue())
    assert headers == ["Rifle", "Velocity"]
    assert rows == [{"Rifle": "Faxon 20in", "Velocity": "2822"}]


def test_unsupported_extension_rejected():
    with pytest.raises(ImportError_):
        parse_uploaded_file("x.txt", b"A,B\n1,2\n")


def test_no_data_rows_rejected():
    with pytest.raises(ImportError_):
        parse_uploaded_file("x.csv", _csv_bytes("A,B\n"))


def test_too_many_rows_rejected():
    text = "A\n" + "\n".join(str(i) for i in range(MAX_IMPORT_ROWS + 1))
    with pytest.raises(ImportError_):
        parse_uploaded_file("x.csv", _csv_bytes(text))


def test_oversized_file_rejected():
    from ballistica.import_export import MAX_IMPORT_FILE_BYTES
    huge = "A\n" + "x" * (MAX_IMPORT_FILE_BYTES + 1)
    with pytest.raises(ImportError_):
        parse_uploaded_file("x.csv", _csv_bytes(huge))


# ----------------------------------------------------------- mapping

def test_suggest_mapping_basic():
    mapping = suggest_mapping(["Rifle", "Bullet", "Velocity", "BC"])
    assert mapping["rifle_name"] == "Rifle"
    assert mapping["bullet_type"] == "Bullet"
    assert mapping["muzzle_velocity_fps"] == "Velocity"
    assert mapping["bc"] == "BC"


def test_suggest_mapping_no_field_steals_another_fields_exact_match():
    """Regression test for a real bug found during manual testing: a
    generic alias on one field ("model" on scope_model) stole a header
    that was an EXACT match for a different field (drag_model's own
    "drag model" alias, against a real "Drag Model" column)."""
    mapping = suggest_mapping(["Drag Model"])
    assert mapping["drag_model"] == "Drag Model"
    assert mapping["scope_model"] is None


def test_suggest_mapping_two_fields_never_claim_the_same_header():
    headers = ["Rifle", "Caliber", "Barrel Length", "Twist", "Bullet", "Grains",
               "BC", "Drag Model", "Powder", "Charge", "Velocity", "Notes"]
    mapping = suggest_mapping(headers)
    matched = [v for v in mapping.values() if v is not None]
    assert len(matched) == len(set(matched))


# --------------------------------------------------------- apply_mapping

def _mapped_row(**overrides) -> tuple[list[dict], dict]:
    row = {
        "Rifle": "Faxon 20in", "Caliber": ".223 Wylde", "Bullet": "77gr SMK",
        "BC": "0.207", "Drag Model": "G7", "Velocity": "2822", "Powder": "H335", "Charge": "22.5",
    }
    row.update(overrides)
    mapping = {
        "rifle_name": "Rifle", "caliber": "Caliber", "bullet_type": "Bullet",
        "bc": "BC", "drag_model": "Drag Model", "muzzle_velocity_fps": "Velocity",
        "powder": "Powder", "powder_charge_gr": "Charge",
    }
    return [row], mapping


def test_successful_row_creates_rifle_and_load():
    rows, mapping = _mapped_row()
    results, touched = apply_mapping(rows, mapping, existing_rifles={})
    assert results[0].status == "created"
    assert "Faxon 20in" in touched
    load = touched["Faxon 20in"].loads["22.5gr H335"]
    assert load.bc == 0.207 and load.drag_model == "G7" and load.muzzle_velocity_fps == 2822.0


def test_load_name_synthesized_when_not_mapped():
    rows, mapping = _mapped_row()
    _, touched = apply_mapping(rows, mapping, existing_rifles={})
    assert list(touched["Faxon 20in"].loads.keys()) == ["22.5gr H335"]


def test_missing_bc_fails_with_clear_reason():
    """This is exactly what happens with Rick's own real Faxon data --
    a genuinely missing BC must fail cleanly, not silently default."""
    rows, mapping = _mapped_row(BC="")
    results, touched = apply_mapping(rows, mapping, existing_rifles={})
    assert results[0].status == "failed"
    assert "ballistic coefficient" in results[0].detail.lower()
    assert touched == {}


def test_missing_velocity_fails():
    rows, mapping = _mapped_row(Velocity="")
    results, _ = apply_mapping(rows, mapping, existing_rifles={})
    assert results[0].status == "failed"
    assert "velocity" in results[0].detail.lower()


def test_missing_rifle_name_fails():
    rows, mapping = _mapped_row(Rifle="")
    results, _ = apply_mapping(rows, mapping, existing_rifles={})
    assert results[0].status == "failed"
    assert "rifle name" in results[0].detail.lower()


def test_invalid_drag_model_fails():
    rows, mapping = _mapped_row(**{"Drag Model": "G3"})
    results, _ = apply_mapping(rows, mapping, existing_rifles={})
    assert results[0].status == "failed"
    assert "G1 or G7" in results[0].detail


def test_second_row_same_rifle_adds_load_not_new_rifle():
    row1, mapping = _mapped_row()
    row2, _ = _mapped_row(Charge="23.0", Velocity="2947")
    results, touched = apply_mapping(row1 + row2, mapping, existing_rifles={})
    assert len(touched) == 1
    rifle = list(touched.values())[0]
    assert set(rifle.loads.keys()) == {"22.5gr H335", "23gr H335"}
    assert results[1].detail == "Added load to existing rifle."


def test_existing_rifle_reused_and_not_overwritten():
    existing = Rifle(name="Faxon 20in", scope_height_in=2.5, caliber="already set")
    rows, mapping = _mapped_row()
    results, touched = apply_mapping(rows, mapping, existing_rifles={"Faxon 20in": existing})
    assert touched["Faxon 20in"] is existing  # same object, mutated in place
    assert existing.scope_height_in == 2.5  # not overwritten by the row's lack of a scope-height column
    assert existing.caliber == "already set"  # not overwritten by the row's "Caliber" value either


def test_missing_scope_height_defaults_to_zero_with_warning():
    rows, mapping = _mapped_row()
    results, touched = apply_mapping(rows, mapping, existing_rifles={})
    assert touched["Faxon 20in"].scope_height_in == 0.0
    assert "scope height" in results[0].detail.lower()


def test_partial_failure_still_imports_valid_rows():
    good, mapping = _mapped_row()
    bad, _ = _mapped_row(BC="", Charge="23.0")
    results, touched = apply_mapping(good + bad, mapping, existing_rifles={})
    assert results[0].status == "created"
    assert results[1].status == "failed"
    assert len(touched["Faxon 20in"].loads) == 1  # only the good row landed


# ------------------------------------------------------------- export

def test_export_csv_injection_protection():
    rifle = Rifle(name="=cmd|/c calc!A1", scope_height_in=2.0)
    rifle.add_load(Load(name="Test", bullet_weight_gr=77, bc=0.2, drag_model="G7",
                         muzzle_velocity_fps=2800, zero_distance_yd=100, notes="+SUM(A1:A9)"))
    csv_text = generate_export_csv([rifle]).decode("utf-8-sig")
    assert "'=cmd" in csv_text
    assert "'+SUM" in csv_text
    # And never unescaped -- a bare "=cmd" or "+SUM" without the leading
    # quote anywhere in the file would mean the escaping was skipped.
    assert ",=cmd" not in csv_text
    assert ",+SUM" not in csv_text


def test_export_rifle_with_no_loads_still_gets_a_row():
    rifle = Rifle(name="Empty Rifle", scope_height_in=2.0)
    csv_text = generate_export_csv([rifle]).decode("utf-8-sig")
    assert "Empty Rifle" in csv_text
    assert csv_text.strip().count("\n") == 1  # header + exactly one data row


def test_export_then_reimport_round_trips():
    rifle = Rifle(name="Faxon 20in", scope_height_in=2.5, caliber=".223 Wylde")
    rifle.add_load(Load(name="22.5gr H335", bullet_weight_gr=77, bc=0.207, drag_model="G7",
                         muzzle_velocity_fps=2822, zero_distance_yd=100, powder="H335", powder_charge_gr=22.5))
    csv_bytes = generate_export_csv([rifle])

    headers, rows = parse_uploaded_file("export.csv", csv_bytes)
    mapping = suggest_mapping(headers)
    assert all(v is not None for v in mapping.values())  # every column maps with zero manual correction

    results, touched = apply_mapping(rows, mapping, existing_rifles={})
    assert results[0].status == "created"
    reimported = touched["Faxon 20in"].loads["22.5gr H335"]
    assert reimported.bc == 0.207
    assert reimported.muzzle_velocity_fps == 2822.0
    assert reimported.powder_charge_gr == 22.5
