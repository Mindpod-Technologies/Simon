"""Optional calendar tools: read events from a Google Calendar secret ICS URL.

Only registered when settings.google_calendar_ics is configured.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import requests

from .base import Tool

log = logging.getLogger(__name__)


def _load_calendar(settings):
    import ics  # imported lazily: optional dependency

    resp = requests.get(settings.google_calendar_ics, timeout=15)
    resp.raise_for_status()
    return ics.Calendar(resp.text)


def _event_start(event) -> datetime | None:
    begin = event.begin
    if begin is None:
        return None
    return begin.datetime


def _events_between(settings, start: date, end: date) -> list:
    cal = _load_calendar(settings)
    out = []
    for event in cal.events:
        dt = _event_start(event)
        if dt is None:
            continue
        if start <= dt.date() < end:
            out.append((dt, event.name or "(untitled event)"))
    out.sort(key=lambda item: item[0])
    return out


def _format(events) -> str:
    if not events:
        return "No events."
    return "\n".join(f"- {dt.strftime('%a %d %b %Y %H:%M')}: {name}" for dt, name in events)


def todays_events(settings) -> str:
    today = date.today()
    return _format(_events_between(settings, today, today + timedelta(days=1)))


def upcoming_events(settings) -> str:
    today = date.today()
    return _format(_events_between(settings, today, today + timedelta(days=7)))


def register_calendar_tools(registry, settings) -> None:
    registry.register(Tool(
        name="todays_events",
        description="List today's calendar events.",
        parameters={"type": "object", "properties": {}, "required": []},
        func=lambda: todays_events(settings),
    ))
    registry.register(Tool(
        name="upcoming_events",
        description="List calendar events for the next 7 days.",
        parameters={"type": "object", "properties": {}, "required": []},
        func=lambda: upcoming_events(settings),
    ))
