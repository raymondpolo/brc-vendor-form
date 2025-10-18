# app/events.py
from flask_socketio import emit, join_room, leave_room
from flask_login import current_user
from app.extensions import socketio
from flask import render_template

@socketio.on('join')
def on_join(data):
    room = f"request_{data['request_id']}"
    join_room(room)
    if current_user.is_authenticated:
        print(f"Client {current_user.name} joined room: {room}")
    else:
        print(f"An anonymous client joined room: {room}")

@socketio.on('leave')
def on_leave(data):
    room = f"request_{data['request_id']}"
    leave_room(room)
    if current_user.is_authenticated:
        print(f"Client {current_user.name} left room: {room}")
    else:
        print(f"An anonymous client left room: {room}")

@socketio.on('connect')
def handle_connect(auth=None):
    if current_user.is_authenticated:
        join_room(str(current_user.id))
        print(f'Client connected and joined personal room: {current_user.id}')
        emit('response', {'data': 'Connected'})
    else:
        print('Client connected (unauthenticated)')


@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')

def notify_user(user_id, data):
    socketio.emit('notification', data, room=str(user_id))

def broadcast_new_note(request_id, note):
    room = f'request_{request_id}'
    # Emit structured JSON for the note so clients can render it however they like
    # Include both a human-friendly app-local string and an ISO-like app-local timestamp
    from app.utils import format_app_dt, convert_to_denver
    date_local = convert_to_denver(note.date_posted) if note.date_posted else None
    payload = {
        'id': note.id,
        'author_name': note.author.name,
        'author_initial': (note.author.name[0].upper() if note.author and note.author.name else ''),
        'text': note.text,
        'date_posted_local': date_local.strftime('%m/%d/%Y at %I:%M %p') if date_local else None,
        'date_posted_iso': format_app_dt(note.date_posted) if note.date_posted else None
    }
    socketio.emit('new_note', {'note': payload}, to=room)