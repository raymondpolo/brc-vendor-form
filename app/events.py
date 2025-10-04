# app/events.py
from flask_socketio import emit, join_room, leave_room
from flask_login import current_user
from app import socketio
from flask import render_template, current_app
from pywebpush import webpush, WebPushException
from app.models import User
import json
from threading import Thread

def on_join(data):
    """
    Handles a client joining a room for a specific work order.
    """
    room = f"request_{data['request_id']}"
    join_room(room)
    if current_user.is_authenticated:
        current_app.logger.info(f"Client {current_user.name} joined room: {room}")
    else:
        current_app.logger.info(f"An anonymous client joined room: {room}")

def on_leave(data):
    """
    Handles a client leaving a room.
    """
    room = f"request_{data['request_id']}"
    leave_room(room)
    if current_user.is_authenticated:
        current_app.logger.info(f"Client {current_user.name} left room: {room}")
    else:
        current_app.logger.info(f"An anonymous client left room: {room}")

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
                    from app import db
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