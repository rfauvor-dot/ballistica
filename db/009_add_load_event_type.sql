-- Adds 'load' to the events table's event_type check constraint
-- (db/005_anonymize_events_at_ingestion.sql originally scoped this to
-- 'solve' and 'calibration' only). Per Rick's explicit instruction
-- (2026-08-30): every load a user saves or enters is automatically
-- anonymized and merged into the aggregate pool as a standard,
-- non-optional part of how the app works -- see
-- MULTI_TENANCY_DESIGN.md §26 and ballistica/waiver.py's new Section 4
-- for the disclosure this implements.
--
-- Purely additive -- widens the constraint, touches no existing rows,
-- no column changes. Safe as a plain ALTER, unlike 005 (which was a
-- deliberate drop-and-recreate specifically because the table was
-- still empty at the time and a genuine shape change was needed).
--
-- Run this in the Supabase SQL Editor, same as every prior migration.

alter table public.events
  drop constraint events_type_valid;

alter table public.events
  add constraint events_type_valid check (event_type in ('solve', 'calibration', 'load'));
