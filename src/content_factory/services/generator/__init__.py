"""Dating batch generator service capsule."""

from content_factory.services.generator.overlay import render_card
from content_factory.services.generator.plan import plan_batch, validate_count
from content_factory.services.generator.profiles import (
    choose_run_id,
    load_profile,
    parse_names,
    profile_slug,
)
from content_factory.services.generator.publish import publish_photo
from content_factory.services.generator.qa import check_video
from content_factory.services.generator.report import _write_report as write_report
from content_factory.services.generator.service import generate_batch, read_profile_cursor
from content_factory.services.generator.texts import TextClient, generate_overlay

# fmt: off
__all__ = [
    "TextClient", "check_video", "choose_run_id", "generate_batch",
    "generate_overlay", "load_profile", "parse_names", "plan_batch",
    "profile_slug", "publish_photo", "read_profile_cursor", "render_card",
    "validate_count", "write_report",
]
# fmt: on
