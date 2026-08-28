-- Tracks whether an account has been auto-played the first-login audio
-- walkthrough yet (Section 1, "Getting Started") -- a simple nullable
-- timestamp on the existing per-user `profiles` table (already RLS-
-- protected, already the designated home for "Ballistica-specific
-- per-user settings that aren't auth data itself" per its own comment
-- in 001_multi_tenant_schema.sql). No row exists for a brand-new
-- account until something writes one (nothing currently does -- see
-- api.py's /v2/walkthrough-status, which upserts on first read); a
-- missing row or a null column both mean the same thing: "hasn't heard
-- it yet." No new RLS policies needed -- profiles_select_own/
-- insert_own/update_own from 001 already cover this column.
--
-- Run this in the Supabase SQL Editor, same as every prior migration --
-- DDL isn't something the REST API can apply itself.

alter table public.profiles
  add column first_walkthrough_played_at timestamptz;
