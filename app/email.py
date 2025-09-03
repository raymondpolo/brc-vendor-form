# app/email.py
from threading import Thread
from flask import current_app
from flask_mail import Message
from app import mail

def send_async_email(app, msg):
    with app.app_context():
        mail.send(msg)

def send_notification_email(subject, recipients, html_body):
    app = current_app._get_current_object()
    msg = Message(subject, sender=app.config['MAIL_DEFAULT_SENDER'], recipients=recipients)
    msg.html = html_body
    thr = Thread(target=send_async_email, args=[app, msg])
    thr.start()
    return thr