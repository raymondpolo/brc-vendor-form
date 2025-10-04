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
        # Defensive logging to get recipient info safely
        recipient_info = "N/A"
        if hasattr(message, 'to') and message.to:
            try:
                # SendGrid's `to` can be a list of objects or other structures
                if isinstance(message.to, list) and len(message.to) > 0:
                    first_recipient = message.to[0]
                    if hasattr(first_recipient, 'email'):
                        recipient_info = first_recipient.email
                elif isinstance(message.to, str):
                    recipient_info = message.to
            except (IndexError, AttributeError) as log_e:
                logger.warning(f"Could not extract recipient email for logging: {log_e}")

        try:
            sg = SendGridAPIClient(app.config['SENDGRID_API_KEY'])
            response = sg.send(message)
            logger.info(f"Email sent to {recipient_info} with status code: {response.status_code}")
        except Exception as e:
            logger.error(f"Failed to send email to {recipient_info}. Error: {e}", exc_info=True)

def send_notification_email(subject, recipients, html_body, text_body=None, attachments=None, cc=None, sender=None):
    """
    Constructs and sends an email using the SendGrid API.
    """
    app = current_app._get_current_object()

    # Safeguard: Do not attempt to send an email with no recipients.
    if not recipients:
        app.logger.warning(f"Attempted to send email with subject '{subject}' but no recipients were provided. Aborting send.")
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