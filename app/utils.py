# app/utils.py
from datetime import datetime
import pytz
from flask import current_app
from flask_login import current_user

def get_user_timezone():
    """Gets the timezone for the current user, defaulting to the app's timezone."""
    if current_user.is_authenticated and hasattr(current_user, 'timezone') and current_user.timezone:
        try:
            return pytz.timezone(current_user.timezone)
        except pytz.UnknownTimeZoneError:
            return pytz.timezone(current_app.config.get('TIMEZONE', 'UTC'))
    return pytz.timezone(current_app.config.get('TIMEZONE', 'UTC'))

def convert_to_user_timezone(dt):
    """Converts a naive datetime object (assumed to be UTC) to the user's local timezone."""
    if not dt:
        return None
    utc_dt = pytz.utc.localize(dt)
    user_tz = get_user_timezone()
    return utc_dt.astimezone(user_tz)

def format_datetime_filter(dt):
    """Formats a datetime object for display in the user's timezone."""
    if not dt:
        return ''
    user_dt = convert_to_user_timezone(dt)
    return user_dt.strftime('%m/%d/%Y at %I:%M %p')