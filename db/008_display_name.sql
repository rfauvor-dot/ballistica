-- Adds an optional display name to `profiles`, so the voice greeting
-- can address a signed-in user by their own chosen name instead of
-- the hardcoded "Rick" left over from the single-tenant era (real
-- issue for any other real account -- see MULTI_TENANCY_DESIGN.md
-- §23). Nullable: unset by default, and the greeting falls back to
-- name-less phrasing when it's not set (see api.py/index.html) rather
-- than requiring it at signup.
--
-- Length bounded (1-40 chars when set) mainly so nothing absurd ends
-- up spoken aloud by TTS or breaking the account-menu layout -- not a
-- meaningful security boundary, just a sanity bound on a field a user
-- fully controls about themselves.
--
-- Run this in the Supabase SQL Editor, same as every prior migration --
-- DDL isn't something the REST API can apply itself.

alter table public.profiles
  add column display_name text,
  add constraint profiles_display_name_length
    check (display_name is null or char_length(display_name) between 1 and 40);
