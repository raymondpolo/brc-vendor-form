# app/__init__.py
import os
from flask import Flask
from config import Config
from app.extensions import db, login_manager, migrate, csrf, socketio

def create_app(config_class=Config):
    """
    The application factory. This function creates and configures the Flask application.
    """
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize extensions with the app
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message_category = 'info'
    migrate.init_app(app, db)
    csrf.init_app(app)
    socketio.init_app(app, cors_allowed_origins="*", message_queue=os.environ.get('REDIS_URL'))

    # Ensure the instance and upload folders exist
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # Register blueprints
    from app.auth import auth as auth_blueprint
    app.register_blueprint(auth_blueprint, url_prefix='/auth')

    from app.main import main as main_blueprint
    app.register_blueprint(main_blueprint)
    
    from app.admin import admin as admin_blueprint
    app.register_blueprint(admin_blueprint, url_prefix='/admin')

    # Import models to ensure they are registered with SQLAlchemy
    from app import models

    # Register context processors
    @app.context_processor
    def inject_notifications():
        from flask_login import current_user
        from app.models import Notification
        if current_user.is_authenticated:
            unread_notifications = Notification.query.filter_by(
                user_id=current_user.id, is_read=False
            ).order_by(Notification.timestamp.desc()).all()
            return dict(unread_notifications=unread_notifications)
        return dict(unread_notifications=[])

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

        @app.cli.command("send-follow-ups")
        def send_follow_ups_command():
            """Sends automated follow-up emails for stalled requests."""
            from app.main.routes import send_automated_follow_ups
            send_automated_follow_ups()

    # CORRECTED: Use a relative import to load the event handlers
    from . import events

    return app