-- Decouples loads from rifles into two independent pools, combined only
-- at use-time (MULTI_TENANCY_DESIGN.md §28, requested 2026-09-04 by
-- Rick: a load like "23.5gr H335" should be definable once and freely
-- paired with either of two rifles sharing a caliber -- e.g. a 7in
-- .300 Blackout subsonic barrel and a 16in .300 Blackout supersonic
-- barrel -- rather than recreated per rifle. The Python model
-- (ballistica/profiles.py) and the whole app layer above it (cli.py,
-- api.py, import_export.py, supabase_store.py) were already rewritten
-- against this new shape before this migration was written; this file
-- is the one remaining piece that only Rick can apply, since the
-- REST/anon-role credentials this codebase runs under can't execute
-- DDL. Run it in the Supabase SQL Editor, same as every prior
-- migration in this directory.
--
-- Existing live data safety: this does not delete or rename any rifle
-- or load row. It only drops the relational link between them
-- (loads.rifle_id, rifles.active_load_id) and the RLS/uniqueness rules
-- built on top of that link -- every rifle and load's own fields
-- (name, ballistic data, scope data) are untouched. After this runs,
-- SupabaseProfileStore.load() resolves the previously-active rifle and
-- previously-active load independently via conversation_state (see
-- that file's own docstring) -- there is deliberately no data
-- migration step here to "carry over" which load was active on which
-- rifle, since that pairing concept no longer exists in the target
-- model at all.

-- ---------------------------------------------- drop old RLS policies
-- Both existing policies (from 007_fix_circular_rls_recursion.sql)
-- reference the rifle_id / active_load_id columns being dropped below
-- -- must go first, or the column drops fail with a dependency error.

drop policy "loads_all_own" on public.loads;
drop policy "rifles_all_own" on public.rifles;

-- The two SECURITY DEFINER cross-reference-ownership functions
-- (007_fix_circular_rls_recursion.sql) existed solely to let each
-- policy above check the OTHER table without triggering circular RLS
-- recursion. With no cross-table reference left at all, there's
-- nothing left for them to check -- dropping them removes the same
-- category of risk they were introduced to close, rather than leaving
-- unused SECURITY DEFINER functions lying around as later attack
-- surface.
drop function if exists public._load_owned_by_and_belongs_to_rifle(uuid, uuid, uuid);
drop function if exists public._rifle_owned_by(uuid, uuid);

-- --------------------------------------------------- drop the linkage
-- rifles.active_load_id's FK must go before the column itself.

alter table public.rifles drop constraint if exists rifles_active_load_fk;
alter table public.rifles drop column if exists active_load_id;

-- loads.rifle_id's FK is part of the column definition (references ...
-- on delete cascade) -- dropping the column drops the constraint with
-- it, no separate `drop constraint` needed first.
alter table public.loads drop column if exists rifle_id;

-- --------------------------------------------- fix the uniqueness rule
-- A load's name only needs to be unique per-user now, not per-rifle --
-- there is no rifle to scope it to anymore.

alter table public.loads drop constraint if exists loads_name_unique_per_rifle;
alter table public.loads add constraint loads_name_unique_per_user unique (user_id, name);

-- ------------------------------------------------ restore simple RLS
-- Back to the original, pre-003 shape for both tables -- a plain
-- user_id = auth.uid() check is sufficient again now that neither
-- table references the other, matching MULTI_TENANCY_DESIGN.md #7.2's
-- two-layer isolation principle without any cross-table check to get
-- wrong.

create policy "loads_all_own" on public.loads
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy "rifles_all_own" on public.rifles
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
