"""Joanneum Aeronautics membership application.

Deliberately does not import ``app`` here: doing so would build a Flask
application whenever any module in the package is imported, which makes the
service layer impossible to use (or test) on its own. Import ``create_app`` from
``aeronautics_members.app`` where it is actually needed.
"""

__all__ = ["create_app"]


def __getattr__(name):
    # Lazy re-export so `from aeronautics_members import create_app` still works
    # without importing the app module on every package import.
    if name == "create_app":
        from .app import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
