"""Language code format shared by configuration, routes and services."""

from __future__ import annotations

import re

# Two or three lowercase Latin letters: ``ru``, ``en``, ``eng``. Uppercase,
# region tags (``ru-RU``) and one-letter codes are rejected.
LANGUAGE_CODE_RE = re.compile(r"^[a-z]{2,3}$")

# One lowercase niche name: ``general``, ``dating``, ``auto-parts``. The same
# format is a directory name under ``content/`` and a profile value.
NICHE_CODE_RE = re.compile(r"^[a-z0-9_-]+$")
