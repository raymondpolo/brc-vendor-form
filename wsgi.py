from gevent import monkey
# Monkey-patching is crucial for gevent to work with standard Python libraries.
# It makes standard I/O operations (like network calls) non-blocking.
monkey.patch_all()

from app import create_app, socketio

app = create_app()

if __name__ == '__main__':
    # This block is for running with `python wsgi.py`.
    # For production, Gunicorn will import the `app` object.
    socketio.run(app)