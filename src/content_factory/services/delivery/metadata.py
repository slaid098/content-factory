"""Video metadata taken from the shared database capsule."""

from __future__ import annotations

from content_factory.db.video_meta import (  # isort: skip  # один AST-узел: явный re-export
    VideoMeta as VideoMeta,
    load_video_meta as load_video_meta,
)
