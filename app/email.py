# app/email.py
import os
from flask import current_app
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Mail, Attachment, FileContent, FileName,
    FileType, Disposition
)
from threading import Thread
import logging
import base64

# Set up a logger for this module for better debugging
logger = logging.getLogger(__name__)

def send_async_email(app, message):
    """
    This function runs in a separate thread and needs its own application context
    to access the Flask app's configuration.
    """
    with app.app_context():
        try:
            # Initialize the SendGrid client with the API key from the app config
            sg = SendGridAPIClient(app.config['SENDGRID_API_KEY'])
            # Send the email using the SendGrid API
            response = sg.send(message)
            # Log the successful sending of the email, including the response status code
            logger.info(f"Email sent to {message.to[0].email} with status code: {response.status_code}")
        except Exception as e:
            # Log the full error for debugging if the email fails to send
            logger.error(f"Failed to send email to {message.to[0].email}. Error: {e}", exc_info=True)

def send_notification_email(subject, recipients, html_body, text_body=None, attachments=None, cc=None, sender=None):
    """
    Constructs and sends an email using the SendGrid API.
    This function is designed to be called from your routes and other parts of the application.
    """
    app = current_app._get_current_object()
    
    # Get the default sender from the app's configuration
    effective_sender = sender or app.config.get('MAIL_DEFAULT_SENDER')
    if not effective_sender:
        logger.error("MAIL_DEFAULT_SENDER is not configured. Cannot send email.")
        return

    # Create the email message object using SendGrid's Mail helper
    message = Mail(
        from_email=effective_sender,
        to_emails=recipients,
        subject=subject,
        html_content=html_body,
        plain_text_content=text_body
    )

    # Add CC recipients if any
    if cc:
        message.cc = cc

    # Add attachments if any
    if attachments:
        for att_data in attachments:
            try:
                with open(att_data['path'], 'rb') as f:
                    data = f.read()
                encoded_file = base64.b64encode(data).decode()
                
                attached_file = Attachment(
                    FileContent(encoded_file),
                    FileName(att_data['filename']),
                    FileType(att_data['mimetype']),
                    Disposition('attachment')
                )
                message.add_attachment(attached_file)
            except Exception as e:
                logger.error(f"Failed to attach file {att_data['filename']}. Error: {e}", exc_info=True)

    # Start the background thread to send the email asynchronously
    Thread(target=send_async_email, args=(app, message)).start()