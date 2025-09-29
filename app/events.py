# app/events.py
from flask_socketio import emit, join_room
from flask_login import current_user
from app import socketio

@socketio.on('connect')
def handle_connect(auth=None): # MODIFIED: Added auth parameter
    """
    Handles a new client connection.
    If the user is authenticated, they join a room specific to their user ID.
    """
    if current_user.is_authenticated:
        join_room(str(current_user.id))
        print(f'Client connected and joined room: {current_user.id}')
        emit('response', {'data': 'Connected'})
    else:
        print('Client connected (unauthenticated)')


@socketio.on('disconnect')
def handle_disconnect():
    """
    Handles a client disconnection.
    """
    print('Client disconnected')

def notify_user(user_id, data):
    """
    Emits a 'notification' event to a specific user's room.
    """
    socketio.emit('notification', data, room=str(user_id))
