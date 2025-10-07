# app/events.py
from flask_socketio import emit, join_room, leave_room
from flask_login import current_user
from app import socketio, db
from flask import render_template, current_app, url_for
from pywebpush import webpush, WebPushException
from app.models import User, Note, Notification, WorkOrder
from app.email import send_notification_email
import json
import re
from threading import Thread

@socketio.on('join')
def on_join(data):
    """
    Handles a client joining a room for a specific work order.
    """
    room = f"request_{data['request_id']}"
    join_room(room)
    if current_user.is_authenticated:
        current_app.logger.info(f"Client {current_user.name} joined room: {room}")

@socketio.on('leave')
def on_leave(data):
    """
    Handles a client leaving a room.
    """
    room = f"request_{data['request_id']}"
    leave_room(room)
    if current_user.is_authenticated:
        current_app.logger.info(f"Client {current_user.name} left room: {room}")

@socketio.on('connect')
def handle_connect(auth=None):
    """
    Handles a new client connection.
    """
    if current_user.is_authenticated:
        join_room(str(current_user.id))
        current_app.logger.info(f'Client connected and joined personal room: {current_user.id}')
        emit('response', {'data': 'Connected'})
    else:
        current_app.logger.info('Client connected (unauthenticated)')


@socketio.on('disconnect')
def handle_disconnect():
    """
    Handles a client disconnection.
    """
    current_app.logger.info('Client disconnected')

def _send_web_push_in_thread(app, user_id, title, body, link):
    """
    This function runs in a background thread and requires its own app context.
    """
    with app.app_context():
        user = User.query.get(user_id)
        if not user:
            app.logger.warning(f"Attempted to send push to non-existent user ID: {user_id}")
            return

        if not user.push_subscriptions.first():
            app.logger.info(f"User {user_id} has no push subscriptions. Skipping push notification.")
            return

        vapid_claims = {"sub": app.config['VAPID_CLAIM_EMAIL']}
        app.logger.info(f"Attempting to send push to {user.push_subscriptions.count()} device(s) for user {user_id}.")

        for sub in user.push_subscriptions:
            try:
                webpush(
                    subscription_info=sub.get_subscription_info(),
                    data=json.dumps({"title": title, "body": body, "url": link}),
                    vapid_private_key=app.config['VAPID_PRIVATE_KEY'],
                    vapid_claims=vapid_claims
                )
                app.logger.info(f"Successfully sent push notification to one device for user {user_id}.")
            except WebPushException as ex:
                app.logger.error(f"Failed to send push notification to user {user_id}. Reason: {ex}")
                if ex.response and ex.response.status_code in [404, 410]:
                    app.logger.info(f"Subscription for user {user_id} is expired/invalid. Deleting.")
                    db.session.delete(sub)
                    db.session.commit()
            except Exception as e:
                app.logger.error(f"An unexpected error occurred while sending push to user {user_id}: {e}", exc_info=True)

def notify_user(user_id, data):
    """
    Emits a 'notification' event via WebSocket and triggers a web push notification in a background thread.
    """
    current_app.logger.info(f"Entering notify_user for user_id: {user_id}")
    
    # 1. In-app (WebSocket) notification
    socketio.emit('notification', data, room=str(user_id))
    current_app.logger.info(f"Emitted socket notification to user {user_id}.")
    
    # 2. Web Push Notification in a background thread
    app = current_app._get_current_object()
    thread = Thread(target=_send_web_push_in_thread, args=(app, user_id, "BRC Vendor Form", data['text'], data['link']))
    thread.start()
    current_app.logger.info(f"Started background thread for web push notification for user_id: {user_id}")

def broadcast_new_note(request_id, note):
    """
    Broadcasts a new note to all clients in the room for the given request_id.
    """
    room = f'request_{request_id}'
    note_html = render_template('partials/note.html', note=note)
    socketio.emit('new_note', {'note_html': note_html}, to=room)

@socketio.on('add_note')
def handle_add_note(data):
    """
    Handles a new note submission from a client via WebSocket.
    """
    if not current_user.is_authenticated:
        return

    text = data.get('text')
    request_id = data.get('request_id')

    if not text or not request_id:
        return

    work_order = WorkOrder.query.get(request_id)
    if not work_order:
        return

    note = Note(text=text, author=current_user, work_order=work_order)
    db.session.add(note)

    # Find mentioned users
    notified_users = set()
    if work_order.author and work_order.author != current_user:
        notified_users.add(work_order.author)

    tagged_names = re.findall(r'@(\w+(?:\s\w+)?)', text)
    for name in tagged_names:
        tagged_user = User.query.filter(User.name.ilike(name.strip())).first()
        if tagged_user:
            if tagged_user not in work_order.viewers:
                work_order.viewers.append(tagged_user)
            if tagged_user != current_user:
                notified_users.add(tagged_user)
    
    db.session.commit()

    # Broadcast the new note to all clients viewing this request
    broadcast_new_note(request_id, note)

    # Send notifications to mentioned users
    for user in notified_users:
        if user:
            notification_text = f'{current_user.name} mentioned you in a note on Request #{work_order.id}'
            notification_link = url_for('main.view_request', request_id=work_order.id)
            
            db_notification = Notification(text=notification_text, link=notification_link, user_id=user.id)
            db.session.add(db_notification)
            
            notify_user(user.id, {'text': notification_text, 'link': notification_link})

    db.session.commit()