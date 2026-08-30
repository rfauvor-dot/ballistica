/*
 * Service worker for offline app-shell support (see MULTI_TENANCY_
 * DESIGN.md §19). Deliberately narrow in scope: it only ever caches
 * and falls back for the static shell (the page itself, the ballistic
 * engine script, icons, the branding image). It never touches API
 * traffic (/v2/*, /waiver, Supabase calls) -- those pass straight
 * through to the network exactly as before, so this can't interfere
 * with auth, rate limiting, or any dynamic response.
 *
 * Strategy is network-first, falling back to cache only when the
 * network request actually fails. This matters: the page (`/`) is
 * server-templated per-request (live Supabase config injected) and
 * deliberately served with no-cache headers so a deploy is never
 * masked by a stale cached copy while online (see api.py's web_ui()
 * comment) -- that guarantee has to survive this service worker
 * unchanged. Falling back to cache ONLY on a genuine network failure
 * preserves it: nothing changes for anyone with a connection, and the
 * cached copy is only ever seen with zero connectivity, at which point
 * no dynamic/live behavior (signup, sign-in, waiver, sync) is reachable
 * anyway -- offline mode is read-only against whatever was last synced.
 */
const CACHE_NAME = "ballistica-shell-v1";
const SHELL_URLS = [
  "/",
  "/manifest.json",
  "/engine.js",
  "/icons/icon-32.png",
  "/icons/icon-180.png",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/images/mr-and-mrs-ballistica.jpg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_URLS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))
    ).then(() => self.clients.claim())
  );
});

function isShellRequest(url) {
  if (url.origin !== self.location.origin) return false;
  return SHELL_URLS.includes(url.pathname);
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || !isShellRequest(url)) return; // let everything else (all API calls) pass through untouched

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        // Only cache a genuinely good response -- a transient 500 during
        // a deploy, or any other error, must never overwrite a known-
        // good cached copy with a broken one.
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
