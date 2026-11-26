"""Read-side response mappers of generator profiles."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal, cast

from content_factory.constants import LANGUAGE_LABELS, language_label
from content_factory.schemas.config.runtime import GeneratorConfig
from content_factory.schemas.content.delivery import ContentType
from content_factory.schemas.profiles.api import (
    LanguageOption,
    ProductionCheck,
    ProductionStep,
    ProfileDeliverySettings,
    ProfileOverlayStyle,
    ProfileRentSettings,
    ProfileResponse,
    ProfileSettings,
    ProfileTextSettings,
    ProfileVideoSettings,
)
from content_factory.schemas.profiles.refill import ProfileRefill
from content_factory.schemas.profiles.registry import ProfileInfo
from content_factory.services import production
from content_factory.services.profiles.reports import latest_run

_DISABLED = ProfileRefill(False, None, None)


def select_profiles(
    profiles: tuple[ProfileInfo, ...],
    language: str | None,
    niche: str | None,
) -> tuple[ProfileInfo, ...]:
    """Return the profiles that match both filters, in id order."""
    return tuple(
        profile
        for profile in profiles
        if (language is None or profile.language == language)
        and (niche is None or profile.niche == niche)
    )


def _views(
    profiles: tuple[ProfileInfo, ...],
    stock: dict[str, int],
    photo_stock: dict[str, int],
    refills: dict[str, ProfileRefill],
    *,
    output_dir: Path,
    global_config: GeneratorConfig,
) -> tuple[ProfileResponse, ...]:
    """Build API responses for the selected profiles, in id order."""
    return tuple(
        _view(
            profile,
            stock.get(profile.id, 0),
            photo_stock.get(profile.id, 0),
            refills.get(profile.id, _DISABLED),
            output_dir,
            global_config,
        )
        for profile in profiles
    )


def _language_options(
    profiles: tuple[ProfileInfo, ...],
) -> tuple[LanguageOption, ...]:
    """Return the curated catalog plus profile codes outside it, by label."""
    labels = dict(LANGUAGE_LABELS)
    for profile in profiles:
        labels.setdefault(profile.language, language_label(profile.language))
    return tuple(
        LanguageOption(code=code, label=label)
        for code, label in sorted(labels.items(), key=lambda item: item[1])
    )


def _view(
    profile: ProfileInfo,
    stock: int,
    photo_stock: int,
    refill: ProfileRefill,
    output_dir: Path,
    global_config: GeneratorConfig,
) -> ProfileResponse:
    """Build the API response of one profile."""
    return ProfileResponse(
        id=profile.id,
        category=profile.category,
        name=profile.name,
        language=profile.language,
        language_label=language_label(profile.language),
        niche=profile.niche,
        title=profile.title,
        summary=profile.summary,
        width=profile.width,
        height=profile.height,
        audio=profile.audio,
        outputs=tuple(
            cast(Literal["video", "photo"], content_type.value)
            for content_type in (ContentType.VIDEO, ContentType.PHOTO)
            if content_type in profile.outputs
        ),
        stock=stock,
        photo_stock=photo_stock,
        target=refill.target if refill.target is not None else global_config.target,
        refill_at=(refill.refill_at if refill.refill_at is not None else global_config.refill_at),
        refill_enabled=refill.enabled,
        target_override=refill.target,
        refill_at_override=refill.refill_at,
        photo_target=refill.photo_target,
        photo_refill_at=refill.photo_refill_at,
        preview_url=f"/api/v1/content/profiles/{profile.category}/{profile.name}/preview",
        last_run=latest_run(output_dir, profile),
    )


def _production(profile: Any) -> tuple[ProductionStep, ...]:
    """Build the recipe view in declaration order with resolved QA stages."""
    return tuple(
        ProductionStep(
            producer=layer.producer,
            type=cast(Literal["video", "photo"], layer.output.value),
            checks=tuple(
                ProductionCheck(
                    name=name,
                    stage=cast(
                        Literal["source", "final"],
                        production.qa.resolve(name).stage,
                    ),
                )
                for name in layer.checks
            ),
        )
        for layer in profile.production
    )


def _settings(profile: Any, info: ProfileInfo) -> ProfileSettings:
    """Build the safe read-only settings groups of one profile."""
    title = info.delivery.title
    return ProfileSettings(
        video=ProfileVideoSettings(
            width=profile.width,
            height=profile.height,
            frames=profile.frames,
            frames_jitter=profile.frames_jitter,
            fps=profile.fps,
            renderer=profile.renderer,
            image=profile.image,
            audio_tracks=len(profile.audio_tracks),
        ),
        rent=ProfileRentSettings(
            gpu=profile.gpu,
            max_cost=profile.max_cost,
            disk_space=profile.rent.disk_space,
            gpu_ram=profile.rent.gpu_ram,
            inet_down=profile.rent.inet_down,
            inet_up_cost=profile.rent.inet_up_cost,
            inet_down_cost=profile.rent.inet_down_cost,
        ),
        text=ProfileTextSettings(
            font=profile.font.name,
            negative_prompt=profile.negative_prompt,
            cfg=profile.cfg,
            style=ProfileOverlayStyle(**asdict(profile.style)),
        ),
        delivery=ProfileDeliverySettings(
            title_mode=title.mode.value,
            title=title.value,
            hashtags=info.delivery.hashtags,
        ),
    )
