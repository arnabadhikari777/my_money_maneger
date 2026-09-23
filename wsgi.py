"""
Entry point for PythonAnywhere's WSGI configuration.
On PythonAnywhere, your web app's WSGI file should contain:

    import sys
    path = '/home/YOURUSERNAME/moneymanager'
    if path not in sys.path:
        sys.path.append(path)
    from wsgi import app as application

(See DEPLOY.md for the full walkthrough.)
"""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
