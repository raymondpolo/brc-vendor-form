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

    # --- SendGrid API Key ---
    SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER')
    SHARED_MAIL_USERNAME = os.environ.get('SHARED_MAIL_USERNAME')

    # --- Push Notifications (VAPID Keys) ---
    def _strip_quotes(val):
        if not val:
            return val
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            return val[1:-1]
        return val

    VAPID_PUBLIC_KEY = _strip_quotes(os.environ.get('VAPID_PUBLIC_KEY'))
    VAPID_PRIVATE_KEY = _strip_quotes(os.environ.get('VAPID_PRIVATE_KEY'))
    VAPID_CLAIM_EMAIL = _strip_quotes(os.environ.get('VAPID_CLAIM_EMAIL'))
    
    # +++ ADD THIS LINE TO FIX HTTP LINKS +++
    PREFERRED_URL_SCHEME = 'https'
    # Application timezone (default to Denver). Use TZ database name like 'America/Denver'.
    TIMEZONE = _strip_quotes(os.environ.get('TIMEZONE')) or 'America/Denver'
    # NOTE: All database DateTime fields default to the app timezone via app.utils.get_denver_now()
    # and templates should use the 'local_dt' Jinja filter to render times in the application timezone.