"""A file the portal stored, found by the path kept for it -- only inside the
folder it was stored in.

The database keeps the path of every stored picture, and the portal writes
each one itself. A backup restored from elsewhere could hold any path,
though, and the address of a public picture must never hand out ``.env`` or
a key: a path outside its folder, or leading out of it, is as good as none.
"""

from pathlib import Path


def stored_file(path, folder):
    """``path`` as a file inside ``folder``, or None."""
    if not path:
        return None
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(Path(folder).resolve()) or not resolved.is_file():
        return None
    return resolved
