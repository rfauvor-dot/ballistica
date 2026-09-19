-- Adds a selectable voice persona to `profiles` (BACKLOG.md "Selectable
-- voice persona (male/female)", raised by Rick 2026-08-23, scoped then,
-- built 2026-09-18 now that multi-tenancy -- the thing it was explicitly
-- sequenced behind -- is done).
--
-- Written defensively rather than a plain `add column` (2026-09-18,
-- confirmed live before writing this): a bare `voice_id` column
-- already exists on `profiles` in production -- writes to it already
-- succeed -- despite no tracked migration file for it anywhere in this
-- repo, presumably added directly at some point during the original
-- 2026-08-23 scoping pass. Its current default/nullability is
-- unknown from here (no SQL access outside the REST API, same
-- constraint as every migration in this project), but confirmed live
-- that the constraint below is genuinely NOT present yet: writing an
-- arbitrary string ('totally-invalid-voice') to it via the real REST
-- API succeeded with no error. This script is safe to run regardless
-- of whichever of those partial states it's actually in -- adds the
-- column only if truly missing, backfills any existing nulls, then
-- guarantees the default/not-null/check constraint either way.
--
-- Restricted to the two values actually wired up server-side
-- (VoiceSpeakIn's voice field in api.py still accepts any OpenAI TTS
-- voice name for internal/manual use -- this constraint is specifically
-- about what a user can pick for themselves via /v2/profile, not a
-- restatement of everything TTS supports). Shimmer is Rick's own pick
-- for the female persona (chosen by ear against all 9 stock voices on
-- real reply phrasing, see api.py's VoiceSpeakIn docstring); Onyx is
-- the male persona he picked the same way, 2026-08-23, judged
-- specifically on a live-fire terse line rather than just a greeting.
--
-- Run this in the Supabase SQL Editor, same as every prior migration --
-- DDL isn't something the REST API can apply itself.

do $$
begin
  if not exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'profiles' and column_name = 'voice_id'
  ) then
    alter table public.profiles add column voice_id text;
  end if;
end $$;

alter table public.profiles alter column voice_id set default 'shimmer';
update public.profiles set voice_id = 'shimmer' where voice_id is null;
alter table public.profiles alter column voice_id set not null;

alter table public.profiles drop constraint if exists profiles_voice_id_valid;
alter table public.profiles
  add constraint profiles_voice_id_valid
    check (voice_id in ('shimmer', 'onyx'));
