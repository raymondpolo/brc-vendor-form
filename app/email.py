from flask import current_app, render_template
from flask_mail import Message
from app import mail
from threading import Thread

def send_async_email(app, msg):
    with app.app_context():
        # The try...except block has been removed.
        # If there's an error (e.g., bad credentials), it will now appear in your Flask terminal.
        mail.send(msg)

def send_notification_email(subject, recipients, text_body, html_body, attachments=None, cc=None, sender=None):
    """
    Standardized function to send notification emails.
    """
    app = current_app._get_current_object()
    
    effective_sender = sender or app.config['MAIL_DEFAULT_SENDER']
    
    msg = Message(subject, sender=effective_sender, recipients=recipients, cc=cc)
    msg.body = text_body
    msg.html = html_body

    if attachments:
        for filename, file_data in attachments:
            msg.attach(filename, file_data.content_type, file_data.read())

    Thread(target=send_async_email, args=(app, msg)).start()