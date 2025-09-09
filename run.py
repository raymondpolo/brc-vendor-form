# run.py
from app import create_app, db
from app.models import User

app = create_app()

@app.shell_context_processor
def make_shell_context():
    """Provides a default context for the `flask shell` command."""
    return {'db': db, 'User': User}

@app.cli.command("create-superuser")
def create_superuser():
    """Creates the default superuser."""
    User.create_default_superuser()

if __name__ == '__main__':
    app.run()