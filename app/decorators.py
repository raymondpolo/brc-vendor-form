# app/decorators.py
from functools import wraps
from flask import abort
from flask_login import current_user

def role_required(roles):
    """
    Decorator that checks if a user has one of the required roles.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in roles:
                abort(403)  # Forbidden
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def admin_required(f):
    """
    A specific role-based decorator for general admin access.
    """
    return role_required(['Admin', 'Scheduler', 'Super User'])(f)