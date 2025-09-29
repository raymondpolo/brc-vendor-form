# run.py
from gevent import monkey
# Monkey-patching is essential for gevent to work correctly with standard Python libraries.
# This must be done at the very beginning of the application's entry point.
monkey.patch_all()

from app import create_app, db, socketio
from app.models import User, WorkOrder
from app.main.routes import send_reminders

app = create_app()

@app.shell_context_processor
def make_shell_context():
    """Provides a default context for the `flask shell` command."""
    return {'db': db, 'User': User, 'WorkOrder': WorkOrder, 'socketio': socketio}

@app.cli.command("create-superuser")
def create_superuser():
    """Creates the default superuser."""
    User.create_default_superuser()

@app.cli.command("send-reminders")
def send_reminders_command():
    """Sends follow-up reminders."""
    send_reminders()

if __name__ == '__main__':
    # Running the application with socketio.run ensures that the gevent server is used,
    # which is now correctly configured for asynchronous tasks thanks to monkey-patching.
    print("Starting Flask-SocketIO server with gevent...")
    socketio.run(app, debug=True)