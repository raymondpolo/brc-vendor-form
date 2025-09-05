from flask_login import current_user
from . import main
from app.models import Notification

@main.app_context_processor
def inject_notifications():
    if current_user.is_authenticated:
        # Correctly query the Notification model for unread notifications
        unread_notifications_count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
        unread_messages_count = current_user.new_messages()
        return dict(unread_notifications_count=unread_notifications_count, unread_messages_count=unread_messages_count)
    return dict(unread_notifications_count=0, unread_messages_count=0)

