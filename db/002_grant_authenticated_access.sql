-- Fixes a real gap found while testing SupabaseProfileStore against the
-- live project: RLS policies restrict WHICH rows a role can see, but
-- Postgres also requires the base table-level GRANT before the
-- `authenticated` role can touch a table at all. 001_multi_tenant_schema.sql
-- created the tables and RLS policies but never granted this, so every
-- request from a real signed-in user failed with:
--   "permission denied for table rifles" (Postgres error 42501)
-- This is a one-time grant, not something the REST API can run itself --
-- DDL/GRANT requires direct SQL Editor access, same as running 001 did.

grant select, insert, update, delete on public.profiles to authenticated;
grant select, insert, update, delete on public.rifles to authenticated;
grant select, insert, update, delete on public.loads to authenticated;
grant select, insert, update, delete on public.conversation_state to authenticated;
grant select, insert, update, delete on public.events to authenticated;
