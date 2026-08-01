# BUILD ORDER — "Unauthorized" latch fix

**For:** Builder (Claude Code, live repo)
**From:** Planner
**Depends on:** BUILD-ORDER-unauthorized-regression-diagnostic.md (findings confirmed: UI-only, orchestrator-never-reached, JWT 24h expiry as trigger, `setToken(null)` + stale `user` state as the latch)

Three changes. **A and B are behavior-neutral bug fixes — merge-on-green.** C is a UX decision — **gated, do not implement without explicit approval.**

---

## Change A — Break the latch (clear `user` state alongside the token)

**Root cause:** on a 401, `setToken(null)` clears storage but never clears the React `user` state in `AuthProvider`. `ProtectedRoute` gates on `user`, which stays truthy in memory, so no redirect to `/login` fires. Every subsequent request goes out with no `Authorization` header and re-401s. Only a manual reload escapes (the `useEffect` re-runs, finds no token, redirects).

**Fix:** wherever `setToken(null)` fires on 401, also clear the auth identity state so `ProtectedRoute` fails its gate and redirects.

- Find the 401 handler that calls `setToken(null)` (likely an axios/fetch interceptor or the auth provider's error path).
- Ensure it also clears `user` (and any other in-memory auth flag `ProtectedRoute` reads) — not just the stored token.
- Verify `ProtectedRoute` gates on something that is now falsy after logout, so redirect-to-`/login` happens without a manual reload.

**Test (this is the regression):**
- `test_401_clears_user_state_and_redirects_to_login` — simulate a 401 mid-session; assert `user` becomes null and the router lands on `/login`, with **no** page reload. This is the exact reported failure; it must be red before the fix and green after.
- `test_expired_token_does_not_latch` — a single 401 does not leave the app issuing header-less requests in a loop.

Merge-on-green.

---

## Change B — Stop swallowing the backend's detail

**Root cause:** the UI substitutes a bare `⚠️ Unauthorized` for whatever the backend actually returned. That's what made this a misdiagnosis risk — the surface reported the failure with total confidence and zero detail, so the real cause (expiry → latch) was invisible from the screen.

**Fix:** surface the backend's error detail (status + message body) instead of a hardcoded "Unauthorized" string. At minimum distinguish "your session expired, please sign in again" from a generic auth failure.

**Test:**
- `test_error_surface_shows_backend_detail_not_hardcoded_string` — a 401 with a known detail body renders that detail, not the literal "Unauthorized".

Merge-on-green.

---

## Change C — 24h silent expiry with no refresh (GATED — decision required)

**Not a bug. A UX question the diagnosis exposed.**

Current behavior: access token TTL is 24h (`access_token_expire_minutes = 60*24`), HS256, no refresh mechanism. After A+B land, expiry becomes graceful — you get bounced to `/login` cleanly instead of latching. But you still get *silently* logged out once a day mid-work.

Options (pick before any code):
1. **Leave it.** 24h + clean redirect is acceptable for a single-user admin UI. Cheapest, and A+B already remove the pain.
2. **Refresh-token flow.** Short-lived access token + longer refresh token, silent renewal. Correct long-term, but it's real auth surface — new endpoint, rotation, storage decisions. Not a one-liner.
3. **Extend TTL / sliding expiry.** Bump the window or refresh-on-activity. Middle ground.

**Recommendation:** ship A+B now, take Option 1 for this cut, and only move to 2 if daily re-login actually annoys you in practice. Don't build a refresh-token system speculatively — it's auth surface, and A+B remove the thing that actually hurt this morning.

**This is an outward-facing auth-behavior change — explicit approval before implementing anything beyond Option 1.**

---

## The latch as a class — worth a decision doc, not part of this order

You've now caught three of these in two days: relay body, calendar liveness, session auth. Same signature every time — **one transient fault converts to a permanent state with no self-clearing path, and the surface reports it with total confidence.** The tell is identical: uniform, mid-session onset, right after something worked.

That's a recognizable failure class, and naming it buys you a checklist ("does this transient condition have a self-clearing path, or does it latch?") to apply proactively instead of rediscovering it a fourth time. Recommend a short decision/design note — `D-NNN` or a design-note — capturing the pattern, the tell, and the two-part fix shape (clear the latched state; preserve the underlying detail). Not blocking this fix; worth doing while it's fresh. I can draft it next session if you want.
