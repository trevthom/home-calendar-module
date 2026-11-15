"""Home Calendar persistence, validation and serialisation.

The tables mirror the original Home Calendar document exactly: one row per
calendar, one row per event, plus a monotonic change counter. Keeping the
shape means the ported frontend keeps its existing wire format, recurrence
rules (``repeat`` + ``repeatCount`` + ``exdates``) and date semantics
(``YYYY-MM-DD`` local dates, ``HH:MM`` local times, no time zones).

Everything lives in the hub's central database through
``shared.database.get_db()``. There are no users, ownership columns or
per-account stores: the calendar is family-shared.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Iterable

from shared.database import get_db
from shared.utilities import clean_text, utc_now

REPEAT_VALUES = ("none", "weekly", "biweekly", "monthly", "yearly")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
COLOUR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS calendar_calendars (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    color TEXT NOT NULL,
    visible INTEGER NOT NULL DEFAULT 1,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS calendar_calendars_order
    ON calendar_calendars (position, created_at);

CREATE TABLE IF NOT EXISTS calendar_events (
    id TEXT PRIMARY KEY,
    calendar_id TEXT,
    title TEXT NOT NULL DEFAULT '',
    event_date TEXT NOT NULL,
    all_day INTEGER NOT NULL DEFAULT 0,
    start_time TEXT NOT NULL DEFAULT '',
    end_time TEXT NOT NULL DEFAULT '',
    repeat TEXT NOT NULL DEFAULT 'none',
    repeat_count INTEGER NOT NULL DEFAULT 0,
    countdown INTEGER NOT NULL DEFAULT 0,
    color TEXT,
    location TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    exdates TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS calendar_events_order
    ON calendar_events (event_date, start_time);

CREATE INDEX IF NOT EXISTS calendar_events_calendar
    ON calendar_events (calendar_id, event_date);

CREATE INDEX IF NOT EXISTS calendar_events_countdown
    ON calendar_events (countdown, event_date);

CREATE TABLE IF NOT EXISTS calendar_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

EVENT_FIELDS = (
    "id",
    "title",
    "date",
    "allDay",
    "startTime",
    "endTime",
    "repeat",
    "repeatCount",
    "countdown",
    "description",
    "location",
    "calendarId",
    "color",
    "exdates",
)

CALENDAR_FIELDS = ("id", "name", "color", "visible")


class ValidationError(ValueError):
    """Raised when a request carries values the calendar cannot store."""


class NotFound(LookupError):
    """Raised when a record does not exist."""


def ensure_schema() -> None:
    """Create this application's tables. Safe to run on every startup."""

    db = get_db()
    db.executescript(SCHEMA_SQL)
    db.commit()


# --------------------------------------------------------------- version ----

def _read_version(db) -> int:
    row = db.execute(
        "SELECT value FROM calendar_meta WHERE key = 'version'"
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0


def _write_version(db, value: int) -> None:
    db.execute(
        """
        INSERT INTO calendar_meta (key, value) VALUES ('version', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(max(0, int(value))),),
    )


def bump_version(db) -> int:
    """Increase the change counter. Callers commit."""

    version = _read_version(db) + 1
    _write_version(db, version)
    return version


def current_version() -> int:
    return _read_version(get_db())


# ------------------------------------------------------------ serialising ---

def _event_row_to_dict(row) -> dict[str, Any]:
    try:
        exdates = json.loads(row["exdates"] or "[]")
    except (TypeError, ValueError):
        exdates = []
    if not isinstance(exdates, list):
        exdates = []
    return {
        "id": row["id"],
        "title": row["title"],
        "date": row["event_date"],
        "allDay": bool(row["all_day"]),
        "startTime": row["start_time"] or "",
        "endTime": row["end_time"] or "",
        "repeat": row["repeat"] or "none",
        "repeatCount": int(row["repeat_count"] or 0),
        "countdown": bool(row["countdown"]),
        "description": row["description"] or "",
        "location": row["location"] or "",
        "calendarId": row["calendar_id"],
        "color": row["color"] or None,
        "exdates": [value for value in exdates if isinstance(value, str)],
    }


def _calendar_row_to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "color": row["color"],
        "visible": bool(row["visible"]),
    }


def list_calendars() -> list[dict[str, Any]]:
    rows = get_db().execute(
        "SELECT * FROM calendar_calendars ORDER BY position ASC, created_at ASC"
    ).fetchall()
    return [_calendar_row_to_dict(row) for row in rows]


def list_events() -> list[dict[str, Any]]:
    rows = get_db().execute(
        """
        SELECT * FROM calendar_events
        ORDER BY event_date ASC, start_time ASC, id ASC
        """
    ).fetchall()
    return [_event_row_to_dict(row) for row in rows]


def snapshot() -> dict[str, Any]:
    return {
        "calendars": list_calendars(),
        "events": list_events(),
        "version": current_version(),
    }


# ------------------------------------------------------------- validation ---

def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _as_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{label} must be a whole number.") from None
    if not minimum <= number <= maximum:
        raise ValidationError(f"{label} must be between {minimum} and {maximum}.")
    return number


def _clean_date(value: Any, label: str) -> str:
    text = clean_text(value, 10)
    if not DATE_RE.fullmatch(text):
        raise ValidationError(f"{label} must be a date like 2026-08-24.")
    try:
        date.fromisoformat(text)
    except ValueError:
        raise ValidationError(f"{label} must be a real calendar date.") from None
    return text


def _clean_time(value: Any, label: str) -> str:
    text = clean_text(value, 5)
    if not text:
        return ""
    if not TIME_RE.fullmatch(text):
        raise ValidationError(f"{label} must be a 24-hour time like 09:30.")
    return text


def _clean_colour(value: Any, label: str) -> str | None:
    if value is None:
        return None
    text = clean_text(value, 7)
    if not text:
        return None
    if not COLOUR_RE.fullmatch(text):
        raise ValidationError(f"{label} must look like #c84a31.")
    return text.lower()


def _clean_exdates(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError("exdates must be a list of dates.")
    cleaned: list[str] = []
    for entry in value:
        key = _clean_date(entry, "Each skipped date")
        if key not in cleaned:
            cleaned.append(key)
    return cleaned


def normalise_event(payload: Any, base: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate and fill an event payload, optionally on top of a base event."""

    if not isinstance(payload, dict):
        raise ValidationError("Event payload must be an object.")

    data: dict[str, Any] = dict(base or {})
    data.update({key: payload[key] for key in EVENT_FIELDS if key in payload})

    event_id = clean_text(data.get("id"), 200)
    if not event_id:
        raise ValidationError("An event needs an id.")

    repeat = clean_text(data.get("repeat"), 16).lower() or "none"
    if repeat not in REPEAT_VALUES:
        raise ValidationError("Choose a valid repeat option.")

    calendar_id = data.get("calendarId")
    calendar_id = clean_text(calendar_id, 200) if calendar_id else None

    start_time = _clean_time(data.get("startTime"), "The start time")
    end_time = _clean_time(data.get("endTime"), "The end time")
    all_day = _as_bool(data.get("allDay"))
    if all_day:
        start_time = ""
        end_time = ""

    return {
        "id": event_id,
        "title": clean_text(data.get("title"), 300),
        "date": _clean_date(data.get("date"), "The date"),
        "allDay": all_day,
        "startTime": start_time,
        "endTime": end_time,
        "repeat": repeat,
        "repeatCount": _as_int(data.get("repeatCount") or 0, "Repeat count", 0, 999),
        "countdown": _as_bool(data.get("countdown")),
        "description": clean_text(data.get("description"), 4000),
        "location": clean_text(data.get("location"), 300),
        "calendarId": calendar_id,
        "color": _clean_colour(data.get("color"), "The event colour"),
        "exdates": _clean_exdates(data.get("exdates")),
    }


def normalise_calendar(payload: Any, base: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValidationError("Calendar payload must be an object.")

    data: dict[str, Any] = dict(base or {})
    data.update({key: payload[key] for key in CALENDAR_FIELDS if key in payload})

    calendar_id = clean_text(data.get("id"), 200)
    if not calendar_id:
        raise ValidationError("A calendar needs an id.")

    name = clean_text(data.get("name"), 80)
    if not name:
        raise ValidationError("Give the calendar a name.")

    colour = _clean_colour(data.get("color"), "The calendar colour")
    if not colour:
        raise ValidationError("Choose a colour for the calendar.")

    visible = _as_bool(data.get("visible", True))
    return {
        "id": calendar_id,
        "name": name,
        "color": colour,
        "visible": 1 if visible else 0,
    }


# ---------------------------------------------------------------- events ----

def get_event(event_id: str) -> dict[str, Any] | None:
    row = get_db().execute(
        "SELECT * FROM calendar_events WHERE id = ?", (event_id,)
    ).fetchone()
    return _event_row_to_dict(row) if row else None


def _insert_event(db, event: dict[str, Any], *, created_at: str | None = None) -> None:
    now = utc_now()
    db.execute(
        """
        INSERT INTO calendar_events (
            id, calendar_id, title, event_date, all_day, start_time, end_time,
            repeat, repeat_count, countdown, color, location, description,
            exdates, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event["id"],
            event["calendarId"],
            event["title"],
            event["date"],
            1 if event["allDay"] else 0,
            event["startTime"],
            event["endTime"],
            event["repeat"],
            event["repeatCount"],
            1 if event["countdown"] else 0,
            event["color"],
            event["location"],
            event["description"],
            json.dumps(event["exdates"]),
            created_at or now,
            now,
        ),
    )


def _update_event_row(db, event: dict[str, Any]) -> None:
    db.execute(
        """
        UPDATE calendar_events SET
            calendar_id = ?, title = ?, event_date = ?, all_day = ?,
            start_time = ?, end_time = ?, repeat = ?, repeat_count = ?,
            countdown = ?, color = ?, location = ?, description = ?,
            exdates = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            event["calendarId"],
            event["title"],
            event["date"],
            1 if event["allDay"] else 0,
            event["startTime"],
            event["endTime"],
            event["repeat"],
            event["repeatCount"],
            1 if event["countdown"] else 0,
            event["color"],
            event["location"],
            event["description"],
            json.dumps(event["exdates"]),
            utc_now(),
            event["id"],
        ),
    )


def upsert_event(payload: Any) -> dict[str, Any]:
    """POST /api/events — the original server replaced the record by id."""

    event = normalise_event(payload)
    db = get_db()
    existing = db.execute(
        "SELECT created_at FROM calendar_events WHERE id = ?", (event["id"],)
    ).fetchone()
    if existing is None:
        _insert_event(db, event)
    else:
        _update_event_row(db, event)
    bump_version(db)
    db.commit()
    return event


def patch_event(event_id: str, patch: Any) -> dict[str, Any]:
    current = get_event(event_id)
    if current is None:
        raise NotFound("That event is no longer in the calendar.")
    merged = normalise_event(patch, base=current)
    db = get_db()
    _update_event_row(db, merged)
    bump_version(db)
    db.commit()
    return merged


def delete_event(event_id: str) -> bool:
    db = get_db()
    cursor = db.execute("DELETE FROM calendar_events WHERE id = ?", (event_id,))
    if cursor.rowcount == 0:
        db.rollback()
        return False
    bump_version(db)
    db.commit()
    return True


def countdown_events() -> list[dict[str, Any]]:
    rows = get_db().execute(
        """
        SELECT * FROM calendar_events
        WHERE countdown = 1
        ORDER BY event_date ASC, start_time ASC, id ASC
        """
    ).fetchall()
    return [_event_row_to_dict(row) for row in rows]


# ------------------------------------------------------------- calendars ----

def get_calendar(calendar_id: str) -> dict[str, Any] | None:
    row = get_db().execute(
        "SELECT * FROM calendar_calendars WHERE id = ?", (calendar_id,)
    ).fetchone()
    return _calendar_row_to_dict(row) if row else None


def upsert_calendar(payload: Any) -> dict[str, Any]:
    calendar = normalise_calendar(payload)
    db = get_db()
    existing = db.execute(
        "SELECT created_at, position FROM calendar_calendars WHERE id = ?",
        (calendar["id"],),
    ).fetchone()

    if existing is None:
        next_position = db.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS next FROM calendar_calendars"
        ).fetchone()["next"]
        db.execute(
            """
            INSERT INTO calendar_calendars
                (id, name, color, visible, position, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                calendar["id"],
                calendar["name"],
                calendar["color"],
                calendar["visible"],
                int(next_position),
                utc_now(),
                utc_now(),
            ),
        )
    else:
        db.execute(
            """
            UPDATE calendar_calendars
            SET name = ?, color = ?, visible = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                calendar["name"],
                calendar["color"],
                calendar["visible"],
                utc_now(),
                calendar["id"],
            ),
        )

    bump_version(db)
    db.commit()
    return {"id": calendar["id"], "name": calendar["name"],
            "color": calendar["color"], "visible": bool(calendar["visible"])}


def patch_calendar(calendar_id: str, patch: Any) -> dict[str, Any]:
    current = get_calendar(calendar_id)
    if current is None:
        raise NotFound("That calendar no longer exists.")
    merged = normalise_calendar(patch, base=current)
    db = get_db()
    db.execute(
        """
        UPDATE calendar_calendars
        SET name = ?, color = ?, visible = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            merged["name"],
            merged["color"],
            merged["visible"],
            utc_now(),
            merged["id"],
        ),
    )
    bump_version(db)
    db.commit()
    return {
        "id": merged["id"],
        "name": merged["name"],
        "color": merged["color"],
        "visible": bool(merged["visible"]),
    }


def delete_calendar(calendar_id: str) -> bool:
    """Remove a calendar and every event that belonged to it (cascade)."""

    db = get_db()
    cursor = db.execute(
        "DELETE FROM calendar_calendars WHERE id = ?", (calendar_id,)
    )
    if cursor.rowcount == 0:
        db.rollback()
        return False
    db.execute("DELETE FROM calendar_events WHERE calendar_id = ?", (calendar_id,))
    bump_version(db)
    db.commit()
    return True


# --------------------------------------------------------------- replace ----

def replace_all(calendars: Iterable[Any], events: Iterable[Any]) -> int:
    """PUT /api/state — wholesale replace, used by .ics import and restore."""

    if not isinstance(calendars, (list, tuple)) or not isinstance(events, (list, tuple)):
        raise ValidationError("The snapshot needs a calendars and an events list.")

    clean_calendars = [normalise_calendar(item) for item in calendars]
    clean_events = [normalise_event(item) for item in events]

    db = get_db()
    try:
        db.execute("DELETE FROM calendar_events")
        db.execute("DELETE FROM calendar_calendars")
        for position, calendar in enumerate(clean_calendars):
            db.execute(
                """
                INSERT INTO calendar_calendars
                    (id, name, color, visible, position, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    calendar["id"],
                    calendar["name"],
                    calendar["color"],
                    calendar["visible"],
                    position,
                    utc_now(),
                    utc_now(),
                ),
            )
        for event in clean_events:
            _insert_event(db, event)
    except Exception:
        db.rollback()
        raise

    version = bump_version(db)
    db.commit()
    return version
