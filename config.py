# config.py
import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
instance_dir = os.path.join(basedir, 'instance')

load_dotenv(os.path.join(basedir, '.env'))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'a-hard-to-guess-string'
    SERVER_NAME = os.environ.get('SERVER_NAME')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(instance_dir, 'site.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_POOL_RECYCLE = 280
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_recycle': 280
    }
    UPLOAD_FOLDER = os.path.join(basedir, 'uploads')
    
    WTF_CSRF_ENABLED = True
    
    # --- Old Flask-Mail settings are removed ---

    # Add the new SendGrid API Key configuration
    SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY')
    
    # We still need a default sender email address
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER')

    SHARED_MAIL_USERNAME = os.environ.get('SHARED_MAIL_USERNAME')