"""Dating batch generator service capsule."""

from content_factory.services.generator.media import probe
from content_factory.services.generator.overlay import mix, render_card
from content_factory.services.generator.plan import plan_batch, validate_count
from content_factory.services.generator.profiles import (
    choose_run_id,
    load_profile,
    parse_names,
    profile_slug,
)
from content_factory.services.generator.publish import publish_photo, publish_stock
from content_factory.services.generator.qa import check_declared
from content_factory.services.generator.report import _write_report as write_report
from content_factory.services.generator.safety import FrameChecker
from content_factory.services.generator.service import generate_batch
from content_factory.services.generator.texts import TextClient, generate_overlay, save_overlay

# fmt: off
__all__ = (
    "FrameChecker", "TextClient", "check_declared", "choose_run_id", "generate_batch",
    "generate_overlay", "load_profile", "mix", "parse_names", "plan_batch", "probe",
    "profile_slug", "publish_photo", "publish_stock", "render_card", "save_overlay",
    "validate_count", "write_report",
)
# fmt: on
