# Tasker: JARVIS Location Push — setup, recovery & version control

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
