from flask import current_app, render_template
from flask_mail import Message
from app import mail
from threading import Thread
import logging

# Set up a logger for this module
logger = logging.getLogger(__name__)

def send_async_email(app, msg):
    """
    This function runs in a separate thread and needs its own application context
    to access the Flask app's configuration and extensions.
    """
    with app.app_context():
        try:
            mail.send(msg)
            logger.info(f"Email sent successfully to {msg.recipients}")
        except Exception as e:
            # Log the full error for debugging. This is crucial for production.
            logger.error(f"Failed to send email to {msg.recipients}. Error: {e}", exc_info=True)

def send_notification_email(subject, recipients, text_body, html_body, attachments=None, cc=None, sender=None):
    """
    Standardized function to send notification emails asynchronously.
    """
    # Get the current Flask app instance to pass to the thread.
    app = current_app._get_current_object()
    
    # Use the default sender from config if a specific sender isn't provided.
    # This now explicitly fetches the sender from the app's config.
    effective_sender = sender or app.config.get('MAIL_DEFAULT_SENDER')
    
    # Defensive check: if no sender is found, log an error and stop.
    if not effective_sender:
        logger.error("MAIL_DEFAULT_SENDER is not configured. Cannot send email.")
        return

    # Create the email message object.
    msg = Message(subject, sender=effective_sender, recipients=recipients, cc=cc)
    msg.body = text_body
    msg.html = html_body

    # Attach any files if they are provided.
    if attachments:
        for filename, file_data in attachments:
            msg.attach(filename, file_data.content_type, file_data.read())

    # Start the background thread to send the email.
    Thread(target=send_async_email, args=(app, msg)).start()