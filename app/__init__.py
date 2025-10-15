# app/__init__.py
import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_mail import Mail
from config import Config
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message_category = 'info'
mail = Mail()
migrate = Migrate()
csrf = CSRFProtect()

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    login_manager.init_app(app)
    mail.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    from app.auth import auth as auth_blueprint
    app.register_blueprint(auth_blueprint, url_prefix='/auth')

    from app.main import main as main_blueprint
    app.register_blueprint(main_blueprint)
    
    from app.admin import admin as admin_blueprint
    app.register_blueprint(admin_blueprint, url_prefix='/admin')

    from app import models

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
        try:
            models.User.create_default_superuser()
        except Exception as e:
            app.logger.info(f"Could not create superuser (this is normal on first run): {e}")


    return app
