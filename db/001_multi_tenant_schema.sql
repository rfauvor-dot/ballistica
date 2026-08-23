-- Ballistica multi-tenant schema — Supabase Postgres.
--
-- Implements MULTI_TENANCY_DESIGN.md as confirmed by Rick 2026-08-23.
-- Run against a fresh Supabase project (auth.users is provided by
-- Supabase Auth already — nothing to create for identity itself).
--
-- Isolation model (§2.2/§7.2 of the design doc): every table is scoped
-- by user_id and carries a row-level security policy using auth.uid(),
-- which reads directly from the verified JWT Supabase issues on every
-- request. This holds even if application-level filtering is buggy or
-- missing entirely — the two-layer isolation the design calls for.
--
-- Deletion/anonymization (§6.2, closed decision): rifles, loads,
-- conversation_state, and profiles all CASCADE on account deletion --
-- they're personally-identifying data that must be gone without
-- exception. events.user_id instead uses ON DELETE SET NULL: the row
-- (the ballistic facts already folded into the aggregate pool) is kept,
-- only the identifying link is severed, atomically, as part of the same
-- database-level delete Supabase performs on auth.admin.deleteUser() --
-- no application code has to coordinate this by hand. (Supabase's own
-- documented pattern for this exact situation: ON DELETE SET NULL
-- instead of CASCADE where the referencing row should outlive the user.)

-- ------------------------------------------------------------- profiles
-- Ballistica-specific per-user settings that aren't auth data itself
-- (Supabase Auth already owns email/credentials/etc. in auth.users).

create table public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  voice_id text not null default 'shimmer',
  created_at timestamptz not null default now()
);

alter table public.profiles enable row level security;

create policy "profiles_select_own" on public.profiles
  for select using (auth.uid() = user_id);
create policy "profiles_insert_own" on public.profiles
  for insert with check (auth.uid() = user_id);
create policy "profiles_update_own" on public.profiles
  for update using (auth.uid() = user_id);
create policy "profiles_delete_own" on public.profiles
  for delete using (auth.uid() = user_id);

-- --------------------------------------------------------------- rifles
-- Mirrors the existing Rifle dataclass fields (ballistica/profiles.py)
-- exactly, so the migration from the current flat JSON file (§6.3 of
-- the design doc — one existing real record, Rick's own profile) is a
-- straightforward field-for-field insert, not a redesign.

create table public.rifles (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  scope_height_in numeric not null,
  caliber text not null default '',
  barrel_length_in numeric,
  twist_rate text not null default '',
  click_value_mrad numeric not null default 0.1,
  reticle_unit text not null default 'MRAD',
  optic_type text not null default '',
  scope_make text not null default '',
  scope_model text not null default '',
  magnification text not null default '',
  objective_lens_mm numeric,
  focal_plane text not null default '',
  reticle_type text not null default '',
  dot_size_moa numeric,
  has_suppressor boolean not null default false,
  suppressor_type text not null default '',
  active_load_id uuid,  -- FK added below, after loads exists
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint rifles_reticle_unit_valid check (reticle_unit in ('MRAD', 'MOA')),
  constraint rifles_optic_type_valid check (optic_type in ('', 'scope', 'red_dot')),
  constraint rifles_name_unique_per_user unique (user_id, name)
);

alter table public.rifles enable row level security;

create policy "rifles_all_own" on public.rifles
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- ---------------------------------------------------------------- loads

create table public.loads (
  id uuid primary key default gen_random_uuid(),
  rifle_id uuid not null references public.rifles(id) on delete cascade,
  -- Denormalized user_id: lets the RLS policy check ownership directly
  -- on this table without a join back through rifles, which keeps the
  -- policy simple and matches the two-layer isolation approach (§7.4's
  -- required direct-database RLS test exercises exactly this).
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  bullet_weight_gr numeric not null,
  bc numeric not null,
  drag_model text not null,
  muzzle_velocity_fps numeric not null,
  zero_distance_yd numeric not null,
  bullet_type text not null default '',
  powder text not null default '',
  powder_charge_gr numeric,
  notes text not null default '',
  created_at timestamptz not null default now(),
  constraint loads_drag_model_valid check (drag_model in ('G1', 'G7')),
  constraint loads_bc_positive check (bc > 0),
  constraint loads_mv_positive check (muzzle_velocity_fps > 0),
  constraint loads_name_unique_per_rifle unique (rifle_id, name)
);

alter table public.loads enable row level security;

create policy "loads_all_own" on public.loads
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

alter table public.rifles
  add constraint rifles_active_load_fk
  foreign key (active_load_id) references public.loads(id) on delete set null;

-- ---------------------------------------------------- conversation_state
-- Replaces the single global BallisticaCLI's in-progress setup/
-- calibration/pending-delete state (§7.3 of the design doc — DB-
-- persisted, not in-memory, justified by this project's own observed
-- restart frequency at current scale). One row per user; state_json
-- holds whatever _SetupSession/_CalibrationSession/_pending_delete
-- currently represent in memory.

create table public.conversation_state (
  user_id uuid primary key references auth.users(id) on delete cascade,
  state_json jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

alter table public.conversation_state enable row level security;

create policy "conversation_state_all_own" on public.conversation_state
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- ---------------------------------------------------------------- events
-- The aggregate-data seam (§3, §7.6) — deliberately dumb per ChatGPT's
-- review: records what's needed, does not build the aggregation system
-- itself. schema_version and aggregated_at added per ChatGPT's specific
-- field list. IMPORTANT application-level requirement, not enforceable
-- by the schema alone: payload must never contain identifying fields --
-- nulling user_id on deletion is only real anonymization if the payload
-- itself was never PII in the first place.

create table public.events (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete set null,
  event_type text not null,
  schema_version int not null default 1,
  payload jsonb not null,
  aggregated_at timestamptz,
  created_at timestamptz not null default now(),
  constraint events_type_valid check (event_type in ('solve', 'calibration'))
);

alter table public.events enable row level security;

-- While user_id is set, only the owning user can see/write their own
-- events. Once anonymized (user_id null on deletion), no user-scoped
-- policy matches the row at all -- only a service-role connection
-- (used by the future aggregate-data project, not by any user session)
-- can read it. This is deliberate: an anonymized row shouldn't be
-- visible to any single user, including whoever it originally belonged
-- to, since it's no longer "theirs" per the closed deletion policy.
create policy "events_all_own" on public.events
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
