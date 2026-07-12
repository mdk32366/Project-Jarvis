"""Portable datetime formatting.

`%-I` (hour, no leading zero) and `%-d` (day, no leading zero) are **glibc
extensions**. They work on Linux and macOS and raise `ValueError: Invalid format
string` on Windows.

Production runs on Linux, so this never broke Fly — but it crashed the test suite
on a Windows dev machine, which is arguably worse: a bug that only appears where
you develop is a bug that costs you time on every run.

These helpers produce the same output everywhere. Use them instead of strftime
for anything spoken aloud, because "oh seven fifteen" is not how a person says a
time.
"""

from __future__ import annotations

from datetime import datetime


def clock(dt: datetime, ampm: bool = True) -> str:
    """'7:15 AM' — no leading zero, spoken naturally.

    Replaces strftime('%-I:%M %p'), which dies on Windows.
    """
    hour = dt.hour % 12 or 12
    out = f"{hour}:{dt.minute:02d}"
    if ampm:
        out += " AM" if dt.hour < 12 else " PM"
    return out


def day(dt: datetime) -> str:
    """'Mon Jul 14' — replaces strftime('%a %b %-d')."""
    return f"{dt.strftime('%a %b')} {dt.day}"


def daytime(dt: datetime) -> str:
    """'Mon Jul 14 at 7:15 AM'."""
    return f"{day(dt)} at {clock(dt)}"


def weekday_clock(dt: datetime) -> str:
    """'Mon 7:15 AM' — replaces strftime('%a %-I:%M %p')."""
    return f"{dt.strftime('%a')} {clock(dt)}"


def month_day(dt: datetime) -> str:
    """'Jul 14' — replaces strftime('%b %-d')."""
    return f"{dt.strftime('%b')} {dt.day}"
