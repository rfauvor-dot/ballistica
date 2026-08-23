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

---

## Seed dataset — published manufacturer ballistic data (bullets/BCs/factory loads)

**Raised:** Rick, 2026-08-23.

**What:** Bundle a starting set of real bullet/BC/factory-load data (Hornady,
Sierra, Berger, Lapua, Nosler, Barnes, etc.) so a new user gets usable data
out of the box instead of starting from zero. Directly closes a real,
verified competitive gap — Strelok Pro advertises 75+ bullets and 55+
factory loads bundled offline; Ballistica currently has none.

**Why it matters:** New-user onboarding friction is a real cost right now —
every rifle/load has to be hand-entered even for extremely common,
well-known factory ammunition. This is table-stakes in the category, not a
differentiator to skip.

**Licensing research (done 2026-08-23, low-risk parallel work while the
multi-tenancy checkpoint sits with Rick for external review):**
- Legal grounding: under U.S. copyright law (*Feist Publications v. Rural
  Telephone*, 1991), raw facts — including measured physical quantities
  like a bullet's weight, diameter, or ballistic coefficient — are not
  copyrightable. Only an *original compilation's specific selection or
  arrangement* can get "thin" copyright protection, and even then only over
  that arrangement, not the underlying numbers. Practical read: the BC/
  weight/velocity *values themselves* are safe to use; copying a
  manufacturer's *table, chart, or page verbatim* is the thing to avoid.
- Searched directly for Hornady/Sierra terms-of-use language on
  reproducing their published ballistic data — found only standard
  load-data liability disclaimers ("use at your own risk"), no explicit
  statement on reproduction rights either way. Practical read: independently
  re-entering the factual values into Ballistica's own schema/format, and
  citing the manufacturer as the data source (for credibility and good
  practice, not because it's legally required for a bare fact), is the
  low-risk path — not scraping/republishing their pages or PDFs as-is.
- Found a real candidate source: `ammolytics/projectiles` on GitHub — MIT-licensed,
  community-maintained dataset covering Barnes/Berger/Hornady/Lapua/Sierra/
  Speer. Appears stale/dormant (work-in-progress, old CI badges), so useful
  as a reference/starting point to evaluate, not something to adopt blindly
  — worth checking what it actually contains before relying on it.
- Caveat, not resolved here: this is a U.S.-law reading. The EU has a
  separate "sui generis" database right that can protect compilations even
  without originality — only relevant if Ballistica ever has EU users/data,
  flagged so it isn't forgotten, not something to solve now.
- **This research is not a substitute for real legal review before a public
  commercial launch** — it's enough to say the idea is viable and worth
  scoping, not enough to skip counsel entirely once real money/liability is
  on the line.

**Status:** Backlog, not scheduled — explicitly does not block or fold into
the current multi-tenancy/security priority. Data structure question (how
seed/reference data relates to per-user profile data) should be resolved
once the multi-tenancy schema is locked, since this is generic reference
data that should live separately from per-user data, not duplicated into
every account.

**Owning lenses when scoped:** Build (schema — likely a separate
read-only/shared reference table, not per-user; sourcing/ingestion
pipeline), Marketing (competitive-parity framing — this is closing a gap,
not building a moat), Legal-adjacent (real counsel review before this data
ships to real customers, per the caveat above).
