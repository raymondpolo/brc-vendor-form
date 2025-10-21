# app/__init__.py
import os
import logging # Import logging
from logging.handlers import RotatingFileHandler
from flask import Flask
from config import Config
from app.extensions import db, login_manager, migrate, csrf, socketio
from app.utils import DENVER_TZ, convert_to_denver # Import Denver timezone and converter

def create_app(config_class=Config):
    """
    The application factory. This function creates and configures the Flask application.
    """
    app = Flask(__name__)
    app.config.from_object(config_class)

    # --- CONFIGURE LOGGING ---
    # Set up a stream handler to output logs to stdout (which Render captures)
    if not app.debug:
        handler = logging.StreamHandler(os.sys.stdout)
        handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s [in %(pathname)s:%(lineno)d]'))

        # Set the log level
        log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
        app.logger.setLevel(log_level)

        app.logger.addHandler(handler)
        app.logger.info('Flask application starting up...') # Test log
    # --- END LOGGING CONFIG ---

    # Initialize extensions with the app
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message_category = 'info'
    migrate.init_app(app, db)
    csrf.init_app(app)

    redis_url = os.environ.get('REDIS_URL')
    # Use logger=True and engineio_logger=True for debugging socket.io
    socketio.init_app(app, cors_allowed_origins="*", message_queue=redis_url, logger=True, engineio_logger=True)

    # Ensure the instance and upload folders exist
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # Register blueprints
    from app.auth import auth as auth_blueprint
    app.register_blueprint(auth_blueprint, url_prefix='/auth')

    from app.main import main as main_blueprint
    # *** VERIFY THIS LINE: Ensure no url_prefix is set for the main blueprint ***
    app.register_blueprint(main_blueprint)
    # *** END VERIFICATION ***

    from app.admin import admin as admin_blueprint
    app.register_blueprint(admin_blueprint, url_prefix='/admin')

    # --- ADD JINJA FILTER ---
    def format_datetime_denver(value, format="%m/%d/%Y %I:%M %p"):
        """Format a datetime object for display, converting to application timezone if necessary.

        This uses the app.config['TIMEZONE'] value implicitly via convert_to_denver which
        currently targets Denver. If TIMEZONE is changed, update app.utils accordingly.
        """
        if value is None:
            return ""
        denver_time = convert_to_denver(value)
        return denver_time.strftime(format)

    # Keep legacy name and add a clearer alias 'local_dt'
    app.jinja_env.filters['format_denver'] = format_datetime_denver
    app.jinja_env.filters['local_dt'] = format_datetime_denver
    # --- END JINJA FILTER ---


    # Import models to ensure they are registered with SQLAlchemy
    from app import models

    # Register context processors
    # Note: The context_processor for notifications is already registered in main/context_processors.py
    # and injected via the main blueprint, so we don't need the simplified version here anymore.

    with app.app_context():
        # Create a default superuser if one doesn't exist
        try:
            models.User.create_default_superuser()
        except Exception as e:
            app.logger.info(f"Could not create superuser (this is normal on first run): {e}")

        # Register shell context processor and CLI commands
        @app.shell_context_processor
        def make_shell_context():
            return {'db': db, 'User': models.User, 'WorkOrder': models.WorkOrder, 'socketio': socketio}

        @app.cli.command("create-superuser")
        def create_superuser():
            """Creates the default superuser."""
            models.User.create_default_superuser()

        @app.cli.command("send-reminders")
        def send_reminders_command():
            """Sends follow-up reminders."""
            from app.main.routes import send_reminders
            send_reminders()

    # CORRECTED: Use a relative import to load the event handlers
    from . import events

    return app