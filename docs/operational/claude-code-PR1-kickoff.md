# Claude Code task — PR-1: minute-granularity quiet hours + briefing exemption

First feature PR of `docs/TDD-jarvis-self-health-loop.md` §7. Small, independently
shippable, leaves the system working. **Now flows through the CI gate you just
proved** — open a branch, watch the `test` job go green on the PR before merge.

Repo: `C:\Projects\Project Jarvis` (canonical git checkout of
`mdk32366/Project-Jarvis`). NOT the snapshot pile.

## Why this PR

The 4 AM briefing call works today via an interim (`QUIET_HOURS_END=3`). This PR
makes the owner's *real* quiet-hours intent expressible — **21:00–03:30, minute
precision** — and exempts the scheduled briefing call from quiet hours entirely,
so the interim can be removed and a brief fires at its configured time
regardless of the window.

TDD-first: write the tests, then implement until green.

## Facts verified against the repo (confirm and proceed)

- `app/config.py` lines 115-116:
  quiet_hours_start: int = 21        # 9pm
  quiet_hours_end: int = 7           # 7am
- `app/channels/outbound_voice.py::in_quiet_hours()` builds
  time(settings.quiet_hours_start, 0) / time(settings.quiet_hours_end, 0) —
  minutes hardcoded to 0. Has a correct wrap-midnight branch.
- `due_calls()` guard: `if r.kind != "callback" and in_quiet_hours(now): continue`
- Existing tests in `tests/test_outbound.py`:
  - test_does_not_ring_at_3am (~line 73) — the pattern to mirror.
  - test_a_callback_the_user_ASKED_for_is_exempt_from_quiet_hours (~85).
  - Tests use monkeypatch.setattr(settings, "quiet_hours_start", 21) etc. and
    ZoneInfo(settings.calendar_timezone) + an `owner` fixture. Match this style.

## Change 1 — minute-granularity quiet hours

In `app/config.py`, add two fields (default 0 -> existing behavior unchanged):
  quiet_hours_start: int = 21
  quiet_hours_start_minute: int = 0
  quiet_hours_end: int = 7
  quiet_hours_end_minute: int = 0

In `in_quiet_hours()`, use them:
  start = time(settings.quiet_hours_start, settings.quiet_hours_start_minute)
  end   = time(settings.quiet_hours_end, settings.quiet_hours_end_minute)
Preserve the wrap-midnight branch exactly.

## Change 2 — exempt the briefing call from quiet hours

In `due_calls()`, change the guard so a scheduled briefing (owner set the time)
is exempt like a callback:
  # A callback or a scheduled briefing the owner set the time for is exempt from
  # quiet hours — in both cases the owner asked for it at this time.
  if r.kind not in ("callback", "briefing") and in_quiet_hours(now):
      continue
Update the `in_quiet_hours` docstring — it says "Applies to briefings and
alerts," no longer true for briefings.

## Tests (write first, in tests/test_outbound.py, mirroring existing style)

| # | Test | Expected |
|---|------|----------|
| 1 | quiet 21:00-03:30, now 03:15 | in_quiet_hours True |
| 2 | quiet 21:00-03:30, now 03:45 | False |
| 3 | quiet 21:00-03:30, now 22:00 | True (wraps midnight) |
| 4 | quiet 21:00-03:30, now 12:00 | False |
| 5 | non-wrap window 13:00-14:00 (minutes 0), now 13:30 | True |
| 6 | existing 21:00-07:00 config, minutes default 0 | identical to before |
| 7 | due_calls: kind="briefing" at 04:00, quiet 21:00-03:30 | returned (NOT suppressed) |
| 8 | due_calls: kind="briefing" at 02:00 (inside quiet) | returned — briefing exempt |
| 9 | due_calls: kind="callback" at 02:00 inside quiet | returned (unchanged) |
| 10 | due_calls: kind="alert" at 02:00 inside quiet | suppressed (unchanged) |

Test 8 is intentional: the exemption means a briefing fires even INSIDE quiet
hours, because the owner sets the briefing time deliberately. Alerts stay
suppressed (test 10).

## Out of scope

- Runtime settings overlay / get_effective (PR-2). These read straight from
  settings for now.
- Any health-check, scheduler-heartbeat, or Admin work.

## After merge + deploy

Owner sets the real window and drops the interim:
  fly secrets set QUIET_HOURS_START=21 QUIET_HOURS_START_MINUTE=0 \
                  QUIET_HOURS_END=3 QUIET_HOURS_END_MINUTE=30 --app jarvis-mdk
(Briefing is exempt regardless, so 04:00 fires even though quiet ends 03:30.)

## Definition of done

- All 10 tests pass; existing test_outbound.py tests still pass (regression).
- in_quiet_hours docstring updated.
- No change outside config.py and outbound_voice.py (+ the test file).
- PR opened; test job green on the PR before merge (the gate in action).
