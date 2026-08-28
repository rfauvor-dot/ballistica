-- URGENT fix for a real bug in 003_close_cross_reference_ownership_gap.sql,
-- found immediately after Rick ran it: that migration's two policies
-- reference each other across tables --
--   rifles_all_own's WITH CHECK queries public.loads (RLS-protected)
--   loads_all_own's USING/WITH CHECK queries public.rifles (RLS-protected)
-- -- a circular RLS dependency. Postgres detects this and raises
-- "infinite recursion detected in policy for relation ..." at evaluation
-- time, which PostgREST surfaces as a bare 500 -- confirmed live: even a
-- plain rifle insert with no active_load_id at all (nothing touching the
-- new cross-reference check) now 500s, because the mere presence of the
-- circular reference breaks policy evaluation for the table, not just
-- the specific column the check is about. This currently means NO
-- rifle or load can be created at all in the live database until this
-- runs -- more urgent than the gap 003 itself was closing.
--
-- Fix: the standard, Supabase-documented pattern for a cross-table RLS
-- check -- wrap each check in a SECURITY DEFINER function. Such a
-- function runs as its owner (not the calling role), which bypasses RLS
-- on the tables it queries internally (RLS applies to the querying
-- role, not table owners, unless FORCE ROW LEVEL SECURITY is set, which
-- nothing in this schema does) -- breaking the circular evaluation
-- while still enforcing exactly the same ownership check 003 intended.
-- `set search_path = public` is a required hardening step for any
-- SECURITY DEFINER function (prevents a caller from shadowing `public`
-- with a malicious search_path to hijack what the function actually
-- queries).
--
-- Run this in the Supabase SQL Editor immediately -- rifle/load
-- creation is broken in the live database until this runs.

create or replace function public._rifle_owned_by(p_rifle_id uuid, p_user_id uuid)
returns boolean
language sql
security definer
set search_path = public
stable
as $$
  select exists (
    select 1 from public.rifles r
    where r.id = p_rifle_id and r.user_id = p_user_id
  );
$$;

create or replace function public._load_owned_by_and_belongs_to_rifle(
  p_load_id uuid, p_user_id uuid, p_rifle_id uuid
)
returns boolean
language sql
security definer
set search_path = public
stable
as $$
  select exists (
    select 1 from public.loads l
    where l.id = p_load_id and l.user_id = p_user_id and l.rifle_id = p_rifle_id
  );
$$;

-- Only the authenticated/service roles that already had table-level
-- access need to be able to call these -- not the public/anon role.
revoke all on function public._rifle_owned_by(uuid, uuid) from public;
revoke all on function public._load_owned_by_and_belongs_to_rifle(uuid, uuid, uuid) from public;
grant execute on function public._rifle_owned_by(uuid, uuid) to authenticated;
grant execute on function public._load_owned_by_and_belongs_to_rifle(uuid, uuid, uuid) to authenticated;

drop policy "loads_all_own" on public.loads;
create policy "loads_all_own" on public.loads
  for all
  using (
    auth.uid() = user_id
    and public._rifle_owned_by(loads.rifle_id, auth.uid())
  )
  with check (
    auth.uid() = user_id
    and public._rifle_owned_by(loads.rifle_id, auth.uid())
  );

drop policy "rifles_all_own" on public.rifles;
create policy "rifles_all_own" on public.rifles
  for all
  using (auth.uid() = user_id)
  with check (
    auth.uid() = user_id
    and (
      active_load_id is null
      or public._load_owned_by_and_belongs_to_rifle(active_load_id, auth.uid(), rifles.id)
    )
  );
