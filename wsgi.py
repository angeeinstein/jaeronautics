"""WSGI entry point (gunicorn runs ``wsgi:application``).

The application is built here rather than at import time inside the package, so
importing a module -- a service, the models, a CLI helper -- does not construct a
Flask app, bind extensions and read configuration as a side effect.
"""

from aeronautics_members.app import create_app

application = create_app()
