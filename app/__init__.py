# app/__init__.py
import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_mail import Mail
from config import Config

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message_category = 'info'
mail = Mail()

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)
    mail.init_app(app)

    # Create upload folder if it doesn't exist
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # Register Blueprints
    from app.auth.routes import auth as auth_blueprint
    app.register_blueprint(auth_blueprint, url_prefix='/auth')

    from app.main.routes import main as main_blueprint
    app.register_blueprint(main_blueprint)

    # Import models to ensure they are known to SQLAlchemy
    from app import models

    # Context processor to inject notifications globally
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
    
    # Create database and default user on first run
    with app.app_context():
        db.create_all()
        models.User.create_default_superuser()

    return app