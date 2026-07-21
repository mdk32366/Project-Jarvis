# Tasker: JARVIS Location Push — setup, recovery & version control

> ## ⛔ SUPERSEDED — the timed-profile approach in this document does not work
>
> **Do not rebuild the 15-minute Time profile described below.** It was established
> on 2026-07-19 that Tasker on this device (Pixel 9) does not appear under
> Settings → Apps → Special app access → **Alarms & reminders**, so it cannot hold
> `SCHEDULE_EXACT_ALARM`. Its scheduled profiles therefore degrade to inexact
> alarms, which Android defers indefinitely while the device is idle.
>
> The symptom shape is worth memorizing, because nothing visible is wrong:
> correct config, correct 15-minute context, **no fires, no errors, empty run log,
> and manual runs perfect.**
>
> Ruled out and not to be relitigated: the per-profile toggle, battery
> optimization, Monitor check intervals, and Tasker version. All four are real
> gotchas; none was the cause.
>
> **The replacement is `docs/TDD-location-pull-inversion.md`:** the server asks and
> the phone answers, over an AutoRemote **Event** profile. There is no schedule on
> the phone anymore — that is the entire point.
>
> **What in this file is still good:** the permissions/battery section, the
> export → scrub-token → commit → paste-token-back workflow, and the recovery
> notes. Those apply unchanged to the new Event profile.
>
> **The task itself is retained — only the Time profile is dead.** Keep a
> *manually run* version (no profile, home-screen shortcut) that posts with no
> nonce and `"trigger":"manual"`, for pre-seeding position before a conversation.
> What was rejected was the false guarantee, not the phone-side task: a timed
> profile claimed to fire and silently didn't, whereas a manual task claims
> nothing and fails visibly to the person pressing it. It cannot mask a broken
> phone either — the responsiveness health check scores request fulfilment, not
> ping recency. See TDD §6.6.

**File:** `jarvis-location-15min.prj.xml` (a Tasker **Project** export)
**What it does:** every 15 minutes → get a GPS fix → HTTP POST it to
`https://jarvis-mdk.fly.dev/api/location` with your shared token.

## ⚠️ Honesty about this file

I built this XML structurally clean and validated it parses, and I based the web
request on Tasker's **stable, well-documented HTTP Post action** rather than the
newer unified HTTP Request (whose exact byte layout I could not verify — that's
the same thing JARVIS's researcher couldn't get right, and I won't bluff it).

**But:** I can't fully verify the `Get Location` action's argument layout against
your exact Tasker version without a real device export. So treat this as
**"most likely imports — verify on first import."** If it fails, the failure is
loud and the fix is quick (see Fallback below). Once it imports and works on
your phone, **re-export it from Tasker and commit *that* file** — the device's
own export is the guaranteed-canonical version.

## Before importing: set the token

Open the file and replace `REPLACE_WITH_LOCATION_TOKEN` with your actual
`LOCATION_TOKEN` value (the one set as a Fly secret and used in the
`X-Jarvis-Token` header). You can also paste it after import by editing the
HTTP Post action's Headers field.

## Import

1. Copy the `.prj.xml` to your phone.
2. Tasker → **long-press anywhere on the bottom nav bar** → **Import Project**.
3. Select `jarvis-location-15min.prj.xml`.
4. You should see a new **JARVIS Location** project with:
   - Profile: **JARVIS Location Push** (Time trigger, every 15 min)
   - Task: **JARVIS Push Location** (Get Location → Wait 10s → HTTP Post)

## Permissions & battery — the part that silently kills it

This is almost certainly why it died before. Do all of these:

- Grant Tasker **Location** permission, set to **Allow all the time** (not "while
  using"). Background location is mandatory for a timed GPS fix.
- Exempt Tasker from **battery optimization**: Android Settings → Apps → Tasker →
  Battery → **Unrestricted**.
- If your phone has an aggressive OEM killer (Samsung, Xiaomi, OnePlus, etc.),
  also **lock/pin Tasker in recents** and disable "put app to sleep."
- Make sure Tasker itself is **On** (top toggle) and the profile is **enabled**
  (green).

## ⚠️ Monitor settings — the invisible silent-killers (learned 2026-07-19)

Today's recovery burned hours on four Tasker behaviors that are **invisible from
the UI**: the profile looks enabled, the run log is empty, and there is **no
error**. If a timed profile that should be firing isn't, check these in order
*before* rebuilding anything — the failure is almost always here, not in the task.

1. **Per-profile enabled toggle ≠ the Profiles-tab "enabled" count.** A profile
   has its own individual on/off, separate from the aggregate count the Profiles
   tab shows. The tab can report profiles enabled while *this one* is individually
   off. Long-press the profile and confirm its own toggle — don't trust the count.

2. **The Monitor check interval must be WELL BELOW the shortest profile interval.**
   Tasker → Preferences → **Monitor** governs how often the background service
   evaluates contexts. Set too high, a timed profile **silently never fires** —
   empty run log, no error, nothing. A 15-minute profile needs a check interval
   far tighter than 15 minutes. This is the single most likely cause of "it just
   stopped and nothing looks wrong."

3. **The Monitor preferences are INTERLOCKED — you can't revert one alone.** Tasker
   enforces relationships between the Monitor settings, roughly:
   - `Wifi scan min ≤ (timeout − 15)`
   - `check interval ≥ timeout`

   So if you changed one during a diagnostic, Tasker will silently clamp or refuse
   your value until you also undo the others in the right order. A half-undone
   session leaves you **stuck** — the field rejects the value you type with no
   explanation. Revert all three together, largest-scope first.

4. **Diagnostic settings are themselves a failure mode — revert them as a
   checklist.** The aggressive values you set to *debug* (tight timeouts, forced
   GPS, loosened wifi minimums, a fast check interval) will quietly degrade normal
   operation and battery if left in place. Before calling the session done,
   deliberately walk back every setting you touched:
   - [ ] Monitor check interval → back to normal (but still below the profile interval)
   - [ ] GPS / monitor timeout → back to default
   - [ ] Wifi scan min → back to default (respecting the `≤ timeout − 15` interlock)
   - [ ] Per-profile toggle → confirmed on
   - [ ] Battery / permission exemptions → still in place (§ above)

> The through-line: **none of these throw an error.** The profile *looks* fine and
> the log is *empty* — which reads as "nothing happened" when the truth is "the
> Monitor never looked." When location goes stale with no obvious cause, suspect
> the Monitor settings before the task itself. (The server-side freshness check
> only sees the *symptom* — no pings arriving — so this doc is the only place the
> real cause is written down.)

## Verify it's pushing

- Force-run the task once (Tasker → task → play button). Then ask JARVIS
  "where am I?" — a fresh fix should read "just now."
- Or watch the server: `fly logs --app jarvis-mdk | grep "location ping"` — you
  want a `location ping: <lat>,<lon>` line.
- The endpoint returns `{"ok": true, "id": N}` on success. A `403` means the
  token is wrong; a `400` means lat/lon didn't come through (see Fallback).

## Fallback if import fails or the fix is empty

The endpoint is deliberately forgiving — it accepts JSON, form-encoded, OR query
params. So if the JSON body or the `Get Location` action gives you trouble,
rebuild the task by hand in ~2 minutes:

1. **Profile:** Time → From 00:00 To 23:59, **repeat every 15 min** → attach a
   new task.
2. **Task, Action 1:** Location → **Get Location** (or **Get Location v2**).
   Leave defaults; timeout ~30s.
3. **Action 2:** Task → **Wait 10 seconds** (lets the fix resolve).
4. **Action 3:** Net → **HTTP Request** (modern) *or* **HTTP Post** (older):
   - Method: **POST**
   - URL: `https://jarvis-mdk.fly.dev/api/location`
   - Headers: `X-Jarvis-Token: <your token>`
   - Body (Content-Type `application/json`):
     `{"lat":%LOC_LAT,"lon":%LOC_LON,"accuracy":%LOC_ACC,"source":"tasker"}`
   - **Simplest of all:** skip the JSON body and use **Query Parameters**
     `lat=%LOC_LAT&lon=%LOC_LON` — the endpoint reads those too.

> Tasker's location variables are `%LOC_LAT`, `%LOC_LON`, `%LOC_ACC` (from Get
> Location). If your version populates `%LOCN` or GPS-specific vars instead,
> adjust the body to match — the endpoint only requires `lat` and `lon`.

## Version control (the actual point)

1. Get it working on the phone.
2. **Re-export from Tasker** (long-press the project tab → Export → XML to
   Storage) — this is the canonical, guaranteed-valid version.
3. Commit that file to `mdk32366/Project-Jarvis` (e.g. `tasker/` or `devices/`).
4. Next phone wipe / OS update / crash = re-import one file, not rebuild from
   memory. That's the whole fix for "the tasks vanished again."
