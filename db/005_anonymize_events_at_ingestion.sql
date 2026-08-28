-- Supersedes the `events` table shape from 001_multi_tenant_schema.sql
-- (§3/§6.2) per Rick's decision, 2026-08-28: aggregate contributions are
-- anonymized AT INGESTION, not tied to the contributing user's account
-- until deletion. The original design kept `events.user_id` set (with
-- `ON DELETE SET NULL` only firing when the account was later deleted) --
-- a deliberate, reasoned decision at the time, but a different one than
-- what Rick described wanting when this was revisited: no traceable link
-- back to the contributing user at all, not even internally, from the
-- moment a contribution enters the pool. See RISK_REGISTER.md and
-- MULTI_TENANCY_DESIGN.md §6.2 for the full history -- this migration is
-- the final, resolved version of that question, not an open one anymore.
--
-- Safe to do as a clean drop-and-recreate, not a careful ALTER: no
-- application code anywhere writes to or reads this table yet (grepped
-- the whole codebase to confirm before this migration was written), and
-- the table itself is empty in production -- confirmed by Rick, no
-- migration/backfill risk. If that stops being true before this runs,
-- STOP and use an additive migration instead (this would destroy any
-- real rows, silently, given how it's written).
--
-- What changed, concretely: `user_id` is gone from the table definition
-- entirely -- not nullable-then-nulled-later, absent. A row written to
-- this table carries no user identifier at any point in its life, so
-- there is nothing to strip on deletion because there was never anything
-- to strip -- if a user deletes their account, their past contributions
-- to this table (if the aggregate pipeline exists by then) require no
-- action at all, because they were never linkable to that account in the
-- first place. Whatever anti-abuse/rate-limiting a future contribution
-- endpoint needs (e.g. "don't let one account flood the pool") is an
-- application-layer, request-time concern -- checked against the
-- authenticated caller's identity at the moment of the API call, never
-- persisted onto the row itself.
--
-- Run this in the Supabase SQL Editor, same as 001-004 -- DDL/policy
-- changes aren't something the REST API can apply itself.

drop table if exists public.events;

create table public.events (
  id uuid primary key default gen_random_uuid(),
  event_type text not null,
  schema_version int not null default 1,
  payload jsonb not null,
  aggregated_at timestamptz,
  created_at timestamptz not null default now(),
  constraint events_type_valid check (event_type in ('solve', 'calibration'))
);

alter table public.events enable row level security;

-- Any authenticated user can contribute -- there's no user_id column to
-- scope an ownership check against (deliberately, see above), so the
-- only thing left to verify is that the request comes from a real
-- authenticated session at all, which running this policy `to
-- authenticated` already enforces via role membership.
create policy "events_insert_any_authenticated" on public.events
  for insert to authenticated with check (true);

-- Deliberately no SELECT/UPDATE/DELETE policy for `authenticated` at
-- all. An anonymous aggregate pool isn't "owned" by anyone, including
-- whoever originally contributed a given row -- there's no per-row
-- ownership check that could even be written, and with RLS enabled and
-- no matching policy, every such query from a normal user session
-- returns zero rows by default (fail-closed, not "everything visible").
-- Reading the raw pool -- for whatever the future aggregate-data
-- project ends up building (a summarized/aggregated view, a scheduled
-- job) -- is a service-role-only concern, never something an individual
-- user session can do directly against this table.

-- IMPORTANT application-level requirement this schema can't enforce on
-- its own, carried over unchanged from the original design: `payload`
-- must never contain identifying fields. Removing `user_id` closes the
-- one *structural* identifying link this schema had; it does nothing
-- to stop a future contribution endpoint from accidentally putting an
-- email, a device id, or similar into `payload` itself. That discipline
-- has to hold wherever code eventually writes to this table.

grant insert on public.events to authenticated;
