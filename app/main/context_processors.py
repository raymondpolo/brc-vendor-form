from flask import current_app
from flask_login import current_user
from . import main
from app.models import Notification, Message
from sqlalchemy import or_

@main.app_context_processor
def inject_notifications():
    if not current_user.is_authenticated:
        # User not logged in: return empty defaults
        return dict(unread_notifications=[], notifications=[], unread_messages_count=0)

    # Authenticated user: load notifications
    unread_notifications = Notification.query.filter_by(user_id=current_user.id, is_read=False).order_by(Notification.timestamp.desc()).all()
    # Also inject a recent full notifications list (both read and unread) so the UI can show history
    notifications = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.timestamp.desc()).limit(50).all()

    shared_email = current_app.config.get('SHARED_MAIL_USERNAME')
    if current_user.role in ['Admin', 'Scheduler', 'Super User'] and shared_email:
        unread_messages_count = Message.query.filter(
            or_(Message.recipient_id == current_user.id, Message.recipient_email == shared_email),
            Message.is_read == False
        ).count()
    else:
        unread_messages_count = Message.query.filter_by(recipient=current_user, is_read=False).count()

    return dict(unread_notifications=unread_notifications, notifications=notifications, unread_messages_count=unread_messages_count)

