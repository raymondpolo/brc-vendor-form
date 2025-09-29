# config.py
import os
from dotenv import load_dotenv

# Establish the base directory for the application.
# This is a robust way to ensure all file paths are correct, regardless of how the app is run.
basedir = os.path.abspath(os.path.dirname(__file__))
instance_dir = os.path.join(basedir, 'instance')

# Load environment variables from a .env file.
load_dotenv(os.path.join(basedir, '.env'))

class Config:
    """
    Main configuration class.
    All application configuration variables are defined here.
    """
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'a-hard-to-guess-string'
    SERVER_NAME = os.environ.get('SERVER_NAME')
    
    # Use the instance folder for the database, which is a Flask best practice.
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(instance_dir, 'site.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Add pool recycling to prevent stale database connections.
    SQLALCHEMY_POOL_RECYCLE = 280
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_recycle': 280
    }
    
    # Define the upload folder relative to the base directory.
    UPLOAD_FOLDER = os.path.join(basedir, 'uploads')
    
    WTF_CSRF_ENABLED = True
    
    # Email server configuration
    MAIL_SERVER = os.environ.get('MAIL_SERVER')
    MAIL_PORT = int(os.environ.get('MAIL_PORT') or 25)
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS') is not None
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER') or MAIL_USERNAME

    # Configuration for a shared mailbox, if used.
    SHARED_MAIL_USERNAME = os.environ.get('SHARED_MAIL_USERNAME')