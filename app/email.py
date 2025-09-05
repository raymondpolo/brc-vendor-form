from flask_mail import Message
from app import mail
from flask import current_app

def send_notification_email(subject, recipients, html_body, text_body=None, sender=None):
    if sender is None:
        sender = current_app.config['ADMINS'][0]
    msg = Message(subject, sender=sender, recipients=recipients)
    msg.body = text_body or 'This is an HTML email. Please use an email client that supports HTML.'
    msg.html = html_body
    mail.send(msg)

