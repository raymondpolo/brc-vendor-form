# app/email.py
import os
from flask import current_app
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from threading import Thread
import logging

logger = logging.getLogger(__name__)

def send_async_email(app, message):
    """
    This function runs in a separate thread and needs its own application context
    to access the Flask app's configuration.
    """
    with app.app_context():
        try:
            sg = SendGridAPIClient(app.config['SENDGRID_API_KEY'])
            response = sg.send(message)
            
            # Defensive logging to prevent crash if message.to is None
            recipient_info = "N/A"
            if message.to and isinstance(message.to, list) and len(message.to) > 0:
                recipient_info = message.to[0].email
            
            logger.info(f"Email sent to {recipient_info} with status code: {response.status_code}")
        except Exception as e:
            recipient_info = "N/A"
            if message.to and isinstance(message.to, list) and len(message.to) > 0:
                recipient_info = message.to[0].email
            logger.error(f"Failed to send email to {recipient_info}. Error: {e}", exc_info=True)

def send_notification_email(subject, recipients, html_body, text_body=None, attachments=None, cc=None, sender=None):
    """
    Constructs and sends an email using the SendGrid API.
    """
    app = current_app._get_current_object()

    # Do not attempt to send an email with no recipients.
    if not recipients:
        app.logger.warning(f"Attempted to send email with subject '{subject}' but no recipients were provided.")
        return

    effective_sender = sender or app.config.get('MAIL_DEFAULT_SENDER')
    if not effective_sender:
        app.logger.error("MAIL_DEFAULT_SENDER is not configured. Cannot send email.")
        return

    message = Mail(
        from_email=effective_sender,
        to_emails=recipients,
        subject=subject,
        html_content=html_body,
        plain_text_content=text_body
    )

    if cc:
        message.cc = cc

    Thread(target=send_async_email, args=(app, message)).start()