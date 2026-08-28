-- Records a user's acceptance of the liability waiver (Ballistica_Liability_
-- Waiver_DRAFT.docx, attorney-approved 2026-08-28 contingent on exactly this
-- flow -- see ballistica/waiver.py for the canonical text/version/hash this
-- table's rows reference). Append-only by design: INSERT and SELECT of a
-- user's own row are the only policies defined below -- no UPDATE, no
-- DELETE. That's deliberate, not an oversight -- a waiver acceptance record
-- that could be edited or removed after the fact by the very user it
-- documents isn't proof of anything. Once written, a row is permanent
-- through the API, for every user including the one it belongs to.
--
-- waiver_version/waiver_sha256 are stored redundantly (not just looked up
-- against the current live waiver) so this row stays meaningful even after
-- the waiver text is later revised -- exactly the "if the waiver text
-- changes later" scenario Rick's instruction called out. accepted_at is the
-- client-captured moment the checkbox was checked and submitted, not the
-- time this row happened to be written (the write can lag the real
-- acceptance moment when Supabase email confirmation is required -- see
-- the frontend flow in index.html/api.py's /v2/waiver/accept).
--
-- user_id CASCADEs on account deletion, matching every other per-user
-- table in this schema (rifles/loads/conversation_state/profiles) rather
-- than the events table's SET NULL/anonymize pattern -- deliberate choice,
-- not the same policy question as the aggregate pool: the stated purpose
-- here is proving which version a user accepted if the text changes later,
-- not surviving that specific user's own account deletion. If Rick or the
-- attorney want acceptance records to survive account deletion for
-- liability-defense purposes once account deletion actually exists as a
-- feature, this is a one-line change (SET NULL instead of CASCADE) --
-- flagged, not decided here, since account deletion isn't built yet either.
--
-- Run this in the Supabase SQL Editor, same as 001/002/003 -- DDL/policy
-- changes aren't something the REST API can apply itself.

create table public.waiver_acceptances (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  waiver_version text not null,
  waiver_sha256 text not null,
  accepted_at timestamptz not null,
  created_at timestamptz not null default now()
);

alter table public.waiver_acceptances enable row level security;

create policy "waiver_acceptances_insert_own" on public.waiver_acceptances
  for insert with check (auth.uid() = user_id);
create policy "waiver_acceptances_select_own" on public.waiver_acceptances
  for select using (auth.uid() = user_id);

grant select, insert on public.waiver_acceptances to authenticated;
