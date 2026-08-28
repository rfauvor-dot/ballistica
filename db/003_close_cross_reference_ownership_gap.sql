-- Closes a real, confirmed-live gap found during post-cutover security
-- hardening (2026-08-28): the existing RLS policies on `loads` and
-- `rifles` only ever checked `user_id = auth.uid()` on the row being
-- written -- neither checked that a RELATED id referenced by that row
-- (loads.rifle_id, rifles.active_load_id) actually belongs to that same
-- user. Ballistica's own app code (supabase_store.py) never constructs
-- such a cross-owner reference -- every id it uses comes from a row it
-- just inserted for the same user in the same save() call -- but a
-- client that talks to Supabase's REST API directly (bypassing
-- Ballistica's app layer entirely, with nothing more than their own
-- valid, legitimately-issued token) was never blocked from doing so.
--
-- Confirmed live, empirically, against the real project before this fix
-- (throwaway script, not committed):
--   1. User B, using their own token, inserted a `loads` row with
--      user_id = B (passes the old WITH CHECK) but rifle_id = a real
--      rifle owned by User A -- succeeded (201).
--   2. User B PATCHed their OWN rifle's active_load_id to point at a
--      real load owned by User A -- succeeded (200).
-- Neither actually let B read/see A's rifle or load data through
-- Ballistica's normal app flow (SupabaseProfileStore.load() only ever
-- matches ids against rows already scoped to the caller's own user_id,
-- so the cross-referenced row/column value just sits unmatched) -- but
-- it's a real violation of "every id referenced in a request must
-- belong to the authenticated user, not just the top-level row," it's
-- reachable by a sophisticated attacker who skips the app and calls
-- PostgREST directly, and it creates dangling rows that only get
-- cleaned up as a side effect of the referenced owner's next save().
-- Not acceptable to leave as "the app doesn't do this" when the schema
-- itself doesn't prevent it -- matches this project's own stated
-- two-layer isolation principle (MULTI_TENANCY_DESIGN.md #7.2): RLS
-- must hold even if application code has a bug (or is bypassed
-- entirely), not just filter correctly when called through the app.
--
-- Run this in the Supabase SQL Editor, same as 001/002 -- DDL/policy
-- changes aren't something the REST API can apply itself.

drop policy "loads_all_own" on public.loads;
create policy "loads_all_own" on public.loads
  for all
  using (
    auth.uid() = user_id
    and exists (
      select 1 from public.rifles r
      where r.id = loads.rifle_id and r.user_id = auth.uid()
    )
  )
  with check (
    auth.uid() = user_id
    and exists (
      select 1 from public.rifles r
      where r.id = loads.rifle_id and r.user_id = auth.uid()
    )
  );

drop policy "rifles_all_own" on public.rifles;
create policy "rifles_all_own" on public.rifles
  for all
  using (auth.uid() = user_id)
  with check (
    auth.uid() = user_id
    and (
      active_load_id is null
      or exists (
        select 1 from public.loads l
        where l.id = rifles.active_load_id
          and l.user_id = auth.uid()
          -- Not just "owned by the same user" -- the load has to
          -- actually belong to THIS rifle. Without this, a user could
          -- point a rifle's active_load_id at a load hanging off a
          -- DIFFERENT one of their own rifles -- not a security issue
          -- (still their own data) but a real data-integrity bug this
          -- fix should close at the same time, for free.
          and l.rifle_id = rifles.id
      )
    )
  );
