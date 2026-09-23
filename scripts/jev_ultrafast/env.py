"""Load Jev's own settings from a .env file without importing anything else from it."""

import os
from pathlib import Path

# Only these names are read. A .env found by walking up from the working directory may belong to an
# unrelated project, and its other secrets must not leak into this process.
PREFIXES = ("TYPESAFE_", "TEXT_MODEL")


def load_env(*extra_dirs):
    """Set Jev's variables from the first .env in the working directory, its parents, then `extra_dirs`.

    Variables already in the environment win. Returns the file that was read, or None.
    """
    for folder in (Path.cwd(), *Path.cwd().parents, *extra_dirs):
        path = folder / ".env"
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            key = key.strip().removeprefix("export ").strip()
            if key.startswith(PREFIXES):
                os.environ.setdefault(key, value.strip().strip('"').strip("'"))
        return path
    return None
