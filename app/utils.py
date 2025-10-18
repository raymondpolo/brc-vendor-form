# app/utils.py
from datetime import datetime, time
import pytz

DENVER_TZ = pytz.timezone('America/Denver')

def get_denver_now():
    """Returns the current datetime localized to America/Denver timezone."""
    return datetime.now(DENVER_TZ)

def convert_to_denver(dt):
    """Converts a naive datetime (assumed UTC) or aware datetime to Denver time."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        # If it's naive, assume it should have been Denver time already based on model defaults
        # Localize it directly to Denver
        dt = DENVER_TZ.localize(dt)
    elif dt.tzinfo != DENVER_TZ:
         # Convert to Denver if it's aware but different (e.g., UTC)
        dt = dt.astimezone(DENVER_TZ)
    return dt

def make_denver_aware_start_of_day(d):
    """Takes a date object and returns a Denver-aware datetime at the start of that day."""
    if d is None:
        return None
    naive_dt = datetime.combine(d, time.min)
    return DENVER_TZ.localize(naive_dt)

def make_denver_aware_end_of_day(d):
    """Takes a date object and returns a Denver-aware datetime at the end of that day."""
    if d is None:
        return None
    naive_dt = datetime.combine(d, time.max)
    return DENVER_TZ.localize(naive_dt)