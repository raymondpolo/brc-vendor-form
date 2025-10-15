# app/events.py
from flask_socketio import emit, join_room, leave_room
from flask_login import current_user
from app.extensions import socketio
from flask import render_template

@socketio.on('join')
def on_join(data):
    """
    Handles a client joining a room for a specific work order.
    This allows us to broadcast updates only to users viewing that request.
    """
    room = f"request_{data['request_id']}"
    join_room(room)
    if current_user.is_authenticated:
        print(f"Client {current_user.name} joined room: {room}")
    else:
        print(f"An anonymous client joined room: {room}")

@socketio.on('leave')
def on_leave(data):
    """
    Handles a client leaving a room when they navigate away from a request page.
    This is good practice to manage resources efficiently.
    """
    room = f"request_{data['request_id']}"
    leave_room(room)
    if current_user.is_authenticated:
        print(f"Client {current_user.name} left room: {room}")
    else:
        print(f"An anonymous client left room: {room}")

@socketio.on('connect')
def handle_connect(auth=None):
    """
    Handles a new client connection.
    If the user is authenticated, they join a room specific to their user ID
    to receive personal notifications.
    """
    if current_user.is_authenticated:
        join_room(str(current_user.id))
        print(f'Client connected and joined personal room: {current_user.id}')
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

def broadcast_new_note(request_id, note):
    """
    Broadcasts a new note to all clients in the room for the given request_id.
    It renders the note HTML on the server to ensure consistency.
    """
    room = f'request_{request_id}'
    # Render the note using a partial template to generate the HTML
    note_html = render_template('partials/note.html', note=note)
    socketio.emit('new_note', {'note_html': note_html}, to=room)