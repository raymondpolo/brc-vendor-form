# run.py
from app import create_app, socketio

# Create the Flask application instance using the factory pattern.
app = create_app()

# Import the events module here to register the Socket.IO event handlers.
# This MUST be done AFTER the app is created to avoid circular imports.
from app import events

if __name__ == '__main__':
    # This block is for local development only.
    # It uses socketio.run to start a development server that fully supports
    # WebSockets and the gevent asynchronous model.
    print("Starting Flask-SocketIO development server...")
    socketio.run(app, debug=True)