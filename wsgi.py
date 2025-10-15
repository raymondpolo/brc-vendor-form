# wsgi.py
# This file is the entry point for the Gunicorn server.

from app import create_app

# Create the Flask application instance.
# Gunicorn will automatically look for this 'app' variable.
app = create_app()

# The events module is now imported within create_app(), so it's not needed here.