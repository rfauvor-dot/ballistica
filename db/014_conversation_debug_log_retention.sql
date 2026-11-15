-- Conversation debug log retention (2026-11-14, Rick's decision): entries
-- older than 30 days are deleted.
--
-- The app already deletes a user's own old entries once a day while they
-- are using voice, and never returns an entry older than 30 days (see
-- supabase_store.py's prune_conversation_log / get_recent_conversation_log).
-- What the app cannot do is reach an account that has stopped using voice:
-- every request it makes uses the signed-in user's own token, so it can only
-- ever touch that user's rows. This scheduled job covers everyone, including
-- accounts that never come back.
--
-- Run once in the Supabase SQL Editor, same as every other file in db/.
-- Uses the pg_cron extension. If the first statement is refused, turn on
-- "pg_cron" under Database > Extensions in the Supabase dashboard, then run
-- this file again. Safe to run more than once: scheduling a job under a name
-- that already exists replaces it.
--
-- NOT YET RUN OR VERIFIED against the live project as of the day it was
-- written -- confirm with the check query at the bottom after running it.

create extension if not exists pg_cron;

-- Makes the daily delete (and the app's own per-user delete) an index range
-- scan instead of a full table scan as the log grows.
create index if not exists conversation_debug_log_created_at_idx
  on public.conversation_debug_log (created_at);

-- Every day at 09:17 UTC.
select cron.schedule(
  'prune-conversation-debug-log',
  '17 9 * * *',
  $$delete from public.conversation_debug_log where created_at < now() - interval '30 days'$$
);

-- To check it is scheduled:
--   select jobname, schedule, active from cron.job where jobname = 'prune-conversation-debug-log';
-- To check it has been running:
--   select status, start_time, return_message from cron.job_run_details
--   where jobid = (select jobid from cron.job where jobname = 'prune-conversation-debug-log')
--   order by start_time desc limit 5;
-- To remove it:
--   select cron.unschedule('prune-conversation-debug-log');
