# app/__init__.py
import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from config import Config
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from flask_socketio import SocketIO

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message_category = 'info'
migrate = Migrate()
csrf = CSRFProtect()
socketio = SocketIO(async_mode='gevent', engineio_logger=True)

def create_app(config_class=Config):
    """
    The application factory. This function creates and configures the Flask application.
    """
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    socketio.init_app(app, cors_allowed_origins="*")

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

    # Import models and events here, AFTER the app and extensions are initialized
    # This avoids the circular import error.
    from app import models, events

    with app.app_context():
        # Create a default superuser if one doesn't exist
        try:
            models.User.create_default_superuser()
        except Exception as e:
            app.logger.info(f"Could not create superuser (this is normal on first run): {e}")

        # Register shell context processor and CLI commands within the app context
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

    return app