# tests/test_timezones.py
import unittest
from app import create_app, db
from app.models import User
from app.utils import convert_to_user_timezone
from datetime import datetime
import pytz

class TimezoneTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_denver_timezone_conversion(self):
        # A datetime object in UTC
        utc_dt = datetime(2025, 6, 15, 18, 0, 0) # 6 PM UTC

        # Create a user with Denver timezone
        user = User(name='Denver User', email='denver@test.com', timezone='America/Denver')
        
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(user)

            # Convert to user's timezone
            denver_dt = convert_to_user_timezone(utc_dt)
            # 12 PM in Denver (MDT is UTC-6)
            self.assertEqual(denver_dt.hour, 12)
            self.assertEqual(denver_dt.tzinfo.zone, 'America/Denver')

    def test_default_timezone_conversion(self):
        # A datetime object in UTC
        utc_dt = datetime(2025, 1, 15, 10, 0, 0) # 10 AM UTC

        # Create a user with no timezone set (should default to app config)
        user = User(name='Default User', email='default@test.com')
        
        with self.app.test_request_context():
            from flask_login import login_user
            login_user(user)

            # Convert to user's timezone
            default_dt = convert_to_user_timezone(utc_dt)
            # 3 AM in Denver (MST is UTC-7)
            self.assertEqual(default_dt.hour, 3)
            self.assertEqual(default_dt.tzinfo.zone, 'America/Denver')

if __name__ == '__main__':
    unittest.main()