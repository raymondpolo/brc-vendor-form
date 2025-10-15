import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))  # <-- Define basedir first
instance_dir = os.path.join(basedir, 'instance')

load_dotenv(os.path.join(basedir, '.env'))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'a-hard-to-guess-string'
    SERVER_NAME = os.environ.get('SERVER_NAME')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(instance_dir, 'site.db')  # use instance_dir here
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.path.join(basedir, 'uploads')
    
    WTF_CSRF_ENABLED = True
    
    MAIL_SERVER = os.environ.get('MAIL_SERVER')
    MAIL_PORT = int(os.environ.get('MAIL_PORT') or 25)
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS') is not None
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER') or MAIL_USERNAME

    SHARED_MAIL_USERNAME = os.environ.get('SHARED_MAIL_USERNAME')
