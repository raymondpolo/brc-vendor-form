# run.py
from app import create_app, db
from app.models import User

# The app factory function creates our Flask app instance
app = create_app()

@app.shell_context_processor
def make_shell_context():
    """Provides a default context for the `flask shell` command."""
    return {'db': db, 'User': User}

if __name__ == '__main__':
    app.run()