# app/utils.py
from datetime import datetime, time
import pytz
import os
from flask import current_app, has_app_context


def _get_timezone():
    """Return a pytz timezone object based on app config TIMEZONE or environment fallback.

    If a Flask application context is active, this will read current_app.config['TIMEZONE'].
    Otherwise it falls back to the TIMEZONE env var or 'America/Denver'.
    """
    tz_name = None
    if has_app_context():
        tz_name = current_app.config.get('TIMEZONE')
    if not tz_name:
        tz_name = os.environ.get('TIMEZONE')
    if not tz_name:
        tz_name = 'America/Denver'
    try:
        return pytz.timezone(tz_name)
    except Exception:
        # Fallback to Denver if the configured timezone is invalid
        return pytz.timezone('America/Denver')


# Backwards-compatible constant for code that imports DENVER_TZ directly.
# This will evaluate to the timezone object for the configured app timezone
# when the module is imported (if no app context, falls back to env or default).
DENVER_TZ = _get_timezone()


def get_denver_now():
    """Returns the current datetime localized to the application's timezone (default Denver)."""
    tz = _get_timezone()
    return datetime.now(tz)


def convert_to_denver(dt):
    """Converts a naive or aware datetime to the application's timezone (default Denver).

    - If dt is naive, it is assumed to represent the application's local time and will be localized.
    - If dt is aware, it will be converted to the application's timezone.
    """
    if dt is None:
        return None
    tz = _get_timezone()
    if dt.tzinfo is None:
        # Many DBs store datetimes without timezone information (naive). In practice
        # these are often stored in UTC or have lost their tzinfo during persistence.
        # To avoid showing times shifted by the wrong offset, assume naive datetimes
        # coming from storage are in UTC, then convert to the application's timezone.
        try:
            return pytz.UTC.localize(dt).astimezone(tz)
        except Exception:
            # Fallback: treat naive as app-local if UTC-localize fails for some reason
            return tz.localize(dt)
    # Convert aware datetimes to the app timezone
    return dt.astimezone(tz)


def make_denver_aware_start_of_day(d):
    """Takes a date object and returns an app-timezone-aware datetime at the start of that day."""
    if d is None:
        return None
    tz = _get_timezone()
    naive_dt = datetime.combine(d, time.min)
    return tz.localize(naive_dt)


def make_denver_aware_end_of_day(d):
    """Takes a date object and returns an app-timezone-aware datetime at the end of that day."""
    if d is None:
        return None
    tz = _get_timezone()
    naive_dt = datetime.combine(d, time.max)
    return tz.localize(naive_dt)


def format_app_dt(dt, fmt="%Y-%m-%dT%H:%M:%S%z"):
    """Return a formatted string for datetime in the application timezone.

    Default format is an ISO-like timestamp with timezone offset. Use a different
    fmt when needed (e.g., '%m/%d/%Y %I:%M %p').
    """
    if dt is None:
        return None
    dt_app = convert_to_denver(dt)
    try:
        return dt_app.strftime(fmt)
    except Exception:
        return None