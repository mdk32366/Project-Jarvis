"""Scheduler hardening (health TDD §6 / §5.2, roadmap R3).

The briefing scheduler used to be an APScheduler cron that bound the time once at
startup — so it couldn't be proven alive, couldn't catch up a missed run, and a
runtime time change needed a redeploy. It's now a per-tick enqueuer:

  * writes a heartbeat every tick (proof-of-life for the §5.2 health check),
  * fires when the effective time has *passed* today and nothing briefed yet
    (missed-run catch-up), guarded against double-fire by last_briefing_date,
  * reads the time from the overlay every tick (hot reschedule — no restart).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings
from app.models import Job, SchedulerHeartbeat
from app.runtime_settings import set_effective
from app.workers import job_worker as jw


def _tz():
    return ZoneInfo(settings.calendar_timezone)


def _at(hour, minute=0):
    """A tz-aware 'now' at hour:minute on a fixed date, in the owner's tz."""
    return datetime(2026, 7, 20, hour, minute, tzinfo=_tz())


def _briefs(db):
    return db.query(Job).filter(Job.kind.in_(["morning_briefing", "briefing_call"])).count()


def _enable(monkeypatch, hour=6, minute=0):
    monkeypatch.setattr(settings, "briefing_enabled", True)
    monkeypatch.setattr(settings, "briefing_hour", hour)
    monkeypatch.setattr(settings, "briefing_minute", minute)


def test_heartbeat_written_each_tick(db, monkeypatch):
    _enable(monkeypatch)
    jw._briefing_tick(db, now=_at(3, 0))         # before scheduled: beat, but no brief
    hb = db.get(SchedulerHeartbeat, 1)
    assert hb is not None and hb.beat_at is not None
    assert hb.enabled is True
    assert hb.next_run_at is not None            # next run is today's 06:00
    assert _briefs(db) == 0


def test_fires_on_time_and_records_last_date(db, monkeypatch):
    _enable(monkeypatch)
    jw._briefing_tick(db, now=_at(6, 0))
    assert _briefs(db) == 1
    assert db.get(SchedulerHeartbeat, 1).last_briefing_date == _at(6, 0).date()


def test_missed_run_catch_up_fires_once(db, monkeypatch):
    """Worker was down at 06:00 and starts at 06:45 — it still briefs once, because
    the fire condition is 'scheduled minute has passed and nothing briefed today',
    not 'the clock equals the minute'."""
    _enable(monkeypatch)
    jw._briefing_tick(db, now=_at(6, 45))
    assert _briefs(db) == 1


def test_no_double_fire_same_day(db, monkeypatch):
    _enable(monkeypatch)
    jw._briefing_tick(db, now=_at(6, 0))
    jw._briefing_tick(db, now=_at(6, 5))
    jw._briefing_tick(db, now=_at(7, 0))
    assert _briefs(db) == 1                       # last_briefing_date guards the rest


def test_disabled_does_not_fire_and_marks_heartbeat(db, monkeypatch):
    monkeypatch.setattr(settings, "briefing_enabled", False)
    jw._briefing_tick(db, now=_at(6, 0))
    assert _briefs(db) == 0
    hb = db.get(SchedulerHeartbeat, 1)
    assert hb.enabled is False
    assert hb.next_run_at is None                 # disabled -> no next run, not 'down'


def test_hot_reschedule_via_overlay(db, monkeypatch):
    """Changing the briefing time through the runtime overlay takes effect on the
    next tick — no restart (the exact R2 limitation R3 removes)."""
    _enable(monkeypatch, hour=6)
    jw._briefing_tick(db, now=_at(5, 30))         # default 06:00 hasn't arrived
    assert _briefs(db) == 0
    set_effective(db, "briefing_hour", 5)         # move it earlier at runtime
    jw._briefing_tick(db, now=_at(5, 31))         # now past the *new* time
    assert _briefs(db) == 1


def test_by_phone_enqueues_a_call(db, monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(settings, "briefing_by_phone", True)
    monkeypatch.setattr(settings, "outbound_calls_enabled", True)
    jw._briefing_tick(db, now=_at(6, 0))
    assert db.query(Job).filter(Job.kind == "briefing_call").count() == 1


def test_tick_never_raises_into_the_loop(db, monkeypatch):
    """A scheduler hiccup must not stop the job queue — the tick swallows errors."""
    _enable(monkeypatch)
    monkeypatch.setattr(jw, "_enqueue_briefing",
                        lambda db: (_ for _ in ()).throw(RuntimeError("boom")))
    jw._briefing_tick(db, now=_at(6, 0))          # must not raise
