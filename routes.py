"""Home Calendar routes, mounted at /calendar by the hub app manager.

The browser keeps the original application's request flow: it renders from
its own cache, then talks to the same small JSON API (state, version, events,
calendars). Only the base path moved, from ``/api`` to ``/calendar/api``.
"""

from __future__ import annotations

from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
    url_for,
)

from . import logic
from .logic import NotFound, ValidationError

blueprint = Blueprint(
    "calendar",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static",
)


@blueprint.after_request
def _revalidate_static(response):
    """Keep this app's assets out of the browser's one-hour cache.

    The hub serves static files with ``max-age=3600``; on a home server these
    files are edited in place, so a stale script could keep running for an hour
    after a fix. Revalidate instead (ETag keeps it a cheap 304).
    """
    if request.endpoint == "calendar.static":
        response.headers["Cache-Control"] = "no-cache"
    return response


def _error(message: str, status: int):
    return jsonify({"error": message}), status


def _payload() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("Send a JSON object.")
    return data


@blueprint.get("/", strict_slashes=False)
def index():
    api_base = url_for("calendar.state").rsplit("/state", 1)[0]
    return render_template("calendar/calendar.html", api_base=api_base)


# ----------------------------------------------------------------- reads ----

@blueprint.get("/api/state")
def state():
    try:
        return jsonify(logic.snapshot())
    except Exception:  # pragma: no cover - defensive, hub renders friendly page
        current_app.logger.exception("Could not read the calendar snapshot")
        return _error("The calendar could not be read just now.", 500)


@blueprint.get("/api/version")
def version():
    try:
        return jsonify({"version": logic.current_version()})
    except Exception:  # pragma: no cover
        current_app.logger.exception("Could not read the calendar version")
        return _error("The calendar could not be read just now.", 500)


# ---------------------------------------------------------------- events ----

@blueprint.post("/api/events")
def create_event():
    try:
        event = logic.upsert_event(_payload())
    except ValidationError as error:
        return _error(str(error), 400)
    return jsonify({"version": logic.current_version(), "event": event})


@blueprint.patch("/api/events/<path:event_id>")
def update_event(event_id: str):
    try:
        event = logic.patch_event(event_id, _payload())
    except ValidationError as error:
        return _error(str(error), 400)
    except NotFound as error:
        return _error(str(error), 404)
    return jsonify({"version": logic.current_version(), "event": event})


@blueprint.delete("/api/events/<path:event_id>")
def delete_event(event_id: str):
    if not logic.delete_event(event_id):
        return _error("That event is no longer in the calendar.", 404)
    return jsonify({"version": logic.current_version()})


# ------------------------------------------------------------- calendars ----

@blueprint.post("/api/calendars")
def create_calendar():
    try:
        calendar = logic.upsert_calendar(_payload())
    except ValidationError as error:
        return _error(str(error), 400)
    return jsonify({"version": logic.current_version(), "calendar": calendar})


@blueprint.patch("/api/calendars/<path:calendar_id>")
def update_calendar(calendar_id: str):
    try:
        calendar = logic.patch_calendar(calendar_id, _payload())
    except ValidationError as error:
        return _error(str(error), 400)
    except NotFound as error:
        return _error(str(error), 404)
    return jsonify({"version": logic.current_version(), "calendar": calendar})


@blueprint.delete("/api/calendars/<path:calendar_id>")
def delete_calendar(calendar_id: str):
    if not logic.delete_calendar(calendar_id):
        return _error("That calendar no longer exists.", 404)
    return jsonify({"version": logic.current_version()})


# --------------------------------------------------------------- replace ----

@blueprint.put("/api/state")
def replace_state():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Send a JSON object with calendars and events.", 400)
    if "calendars" not in data or "events" not in data:
        return _error("Send both calendars and events.", 400)
    try:
        version = logic.replace_all(data["calendars"], data["events"])
    except ValidationError as error:
        return _error(str(error), 400)
    return jsonify({"version": version})


@blueprint.errorhandler(ValidationError)
def _validation_error(error):
    return _error(str(error), 400)
