# run.py
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
    # This is for local development only.
    # Production servers should use a Gunicorn command with gevent.
    socketio.run(app, debug=True)
