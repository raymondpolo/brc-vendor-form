from flask import current_app
from flask_login import current_user
from . import main
from app.models import Notification, Message
from sqlalchemy import or_

@main.app_context_processor
def inject_notifications():
    if current_user.is_authenticated:
        unread_notifications = Notification.query.filter_by(user_id=current_user.id, is_read=False).order_by(Notification.timestamp.desc()).all()
        
        shared_email = current_app.config.get('SHARED_MAIL_USERNAME')
        if current_user.role in ['Admin', 'Scheduler', 'Super User'] and shared_email:
            unread_messages_count = Message.query.filter(
                or_(Message.recipient_id == current_user.id, Message.recipient_email == shared_email),
                Message.is_read == False
            ).count()
        else:
            unread_messages_count = Message.query.filter_by(recipient=current_user, is_read=False).count()
            
        return dict(unread_notifications=unread_notifications, unread_messages_count=unread_messages_count)
    
    return dict(unread_notifications=[], unread_messages_count=0)

