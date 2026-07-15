# Tasker durable fix — handoff & sequencing

This is a **you-on-the-phone** task, not a Claude Code task. It's sequenced here
because the self-health loop depends on it: the `location_pings` freshness check
(TDD-jarvis-self-health-loop §5.3) can only report "no pings in N hours" if pings
actually arrive when they should. A working, durable Tasker push is the *signal
source* that check watches. No pings → the check has nothing to verify against.

## The plan (yours, confirmed correct)

1. **Recover the shape you know worked** (last Sunday's Profile + Task). You said
   it worked in a proven, checkable way — trust that over my generated `.prj.xml`,
   which I could only validate as "most likely imports," not "proven."
2. **Make sure it lives inside a named Project.** The reason it "didn't save to a
   file" before is almost certainly that the Profile/Task weren't wrapped in a
   named Project — Tasker exports *Projects*, and loose profiles/tasks have no
   `.prj.xml` to export. Create/confirm a project named e.g. "JARVIS Location"
   and put the profile + task inside it.
3. **Fix the permissions/battery gotchas** that killed it before (the durability
   half):
   - Tasker Location permission → **Allow all the time** (background).
   - Tasker → Battery → **Unrestricted** (no optimization).
   - OEM killers (Samsung/Xiaomi/etc.): lock Tasker in recents, disable "put app
     to sleep."
   - Confirm Tasker's top toggle is On and the profile is enabled (green).
4. **Verify it's pushing** (the checkable part):
   - Force-run the task, then ask JARVIS "where am I?" → should read "just now."
   - Or watch the server: `fly logs --app jarvis-mdk` and look for a
     `location ping: <lat>,<lon>` line.
5. **Export from the phone** → long-press the project tab → Export → XML to
   Storage. This is the canonical, guaranteed-valid artifact (Tasker serialized
   its own working state — no guessing about action codes).
6. **Commit the phone's export** to `mdk32366/Project-Jarvis` (e.g. `tasker/` or
   `devices/`). This is the durable fix: next wipe/OS-update = re-import one file.

## How this connects to the loop (why it's sequenced before the health checks)

- The `location_pings` component (TDD §4.1 seed) has `check_type=freshness`.
- Its check: time since last `LocationPing` > threshold → `status=degraded`,
  `fault_code=stale`.
- Its remediation row (TDD §4.2 seed) points right back at this document.
- So: get Tasker durable now → the freshness check has real signal later →
  "phone stopped reporting" becomes a surfaced warning in the brief/Admin instead
  of a 46-hour silent gap you discover by asking "where am I?".

## Open decision this surfaces (feeds TDD §11 #2)

The freshness threshold only means something during hours you expect to be
moving. Before the check is built, decide:
- What's the staleness threshold? (e.g. 2 hours)
- Does it only apply during "active hours," or always? (A parked phone overnight
  legitimately sends nothing — you don't want a 3 AM "no pings" false alarm.)
- Is the threshold a runtime setting (so you can tune it without a redeploy)?

Capture your answer; it's an input to the freshness check's PR.

## My generated files (fallback only)

`jarvis-location-15min.prj.xml` and `tasker-setup-and-recovery.md` remain as a
fallback if you *can't* recover Sunday's working shape. But your proven artifact
beats my plausible one — use mine only if the recovery path fails.
