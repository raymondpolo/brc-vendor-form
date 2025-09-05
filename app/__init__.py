from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_mail import Mail
from config import Config
from whitenoise import WhiteNoise

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'info'
mail = Mail()

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize extensions
    db.init_app(app)
    login_manager.init_app(app)
    mail.init_app(app)

    # Add WhiteNoise middleware to serve static files in production
    app.wsgi_app = WhiteNoise(app.wsgi_app, root='app/static/')

    # Import and register blueprints
    from app.main.routes import main as main_blueprint
    app.register_blueprint(main_blueprint)

    from app.auth.routes import auth as auth_blueprint
    app.register_blueprint(auth_blueprint, url_prefix='/auth')
    
    from app.main.context_processors import inject_notifications
    app.context_processor(inject_notifications)

    with app.app_context():
        db.create_all()
        from app.models import User
        User.create_default_superuser()

    return app
