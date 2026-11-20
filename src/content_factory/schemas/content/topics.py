"""HTTP response bodies of the stock topics endpoint."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class TopicCount(BaseModel):
    """Number of stock videos of one search topic."""

    model_config = ConfigDict(frozen=True)

    query: str
    count: int


class TopicsResponse(BaseModel):
    """Stock breakdown by topic for one language."""

    model_config = ConfigDict(frozen=True)

    language: str
    topics: tuple[TopicCount, ...]
    unclassified: int
