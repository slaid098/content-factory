"""Database reads re-exported for the delivery capsule core.

The capsule entry owns the database imports; core modules import these names
from here instead of reaching into ``db.*`` themselves.
"""

from content_factory.services.delivery.service import (  # isort: skip  # один AST-узел: явный re-export
    load_language_queries as load_language_queries,
    load_video_meta as load_video_meta,
)
