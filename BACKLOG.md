# Ballistica Backlog

Future items that are real and worth keeping, but explicitly not being built
now. Each entry: what, why, status/sequencing, owning lenses. Move an item
out of this file into active work (or into an addendum) when it's actually
scoped, don't let it just accumulate here indefinitely.

---

## Spreadsheet / CSV data import

**Raised:** Rick, 2026-08-23.

**What:** Let shooters import existing ballistic data (rifle profiles, load
data, shooting logs) via spreadsheet/CSV upload, instead of manually
re-entering everything from scratch or from another app/spreadsheet they
already track it in.

**Why it matters (two benefits):**
1. Onboarding friction — shooters already tracking their own data elsewhere
   can bring it straight in, lowering the barrier to switching to Ballistica.
2. Aggregate-data strategy — fast-tracks real-world ballistic data volume
   instead of waiting for it to accumulate organically through months of
   live usage.

**Status:** Backlog, not scheduled. Explicitly sequenced **after** the
multi-tenant architecture ([MULTI_TENANCY_DESIGN.md](MULTI_TENANCY_DESIGN.md)),
for two reasons: imported data needs to land in correctly-isolated per-user
storage that doesn't exist yet, and any aggregate use of imported data needs
to go through the same data-quality/categorization discipline flagged for
the aggregate-data project generally — arbitrary spreadsheet data can't be
dumped in unchecked (malformed rows, unit mismatches, no way to distinguish
a shooter's real chronograph data from a guessed value, etc.).

**Owning lenses when scoped:** Build (import/validation pipeline, mapping
arbitrary spreadsheet layouts to Ballistica's schema), Marketing
(onboarding-friction framing, whether this is a launch feature or a
retention feature), Security (an upload endpoint is a new attack surface —
malformed/oversized/malicious file handling needs real scrutiny, not an
afterthought).
