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
    # MODIFIED: Generate HTML directly instead of using a missing template
    note_html = f"""
    <div class="flex space-x-3">
        <div class="flex-shrink-0">
            <img class="h-8 w-8 rounded-full" src="https://placehold.co/100x100/E2E8F0/4A5568?text={note.author.name[0].upper()}" alt="User Avatar">
        </div>
        <div>
            <div class="text-sm">
                <a href="#" class="font-medium text-text">{note.author.name}</a>
            </div>
            <div class="mt-1 text-sm text-text-secondary">
                <p>{note.text}</p>
            </div>
            <div class="mt-2 text-xs text-text-subtle">
                <span>{{ note.date_posted | local_dt('%m/%d/%Y at %I:%M %p') }}</span>
            </div>
        </div>
    </div>
    """
    socketio.emit('new_note', {'note_html': note_html.strip()}, to=room)