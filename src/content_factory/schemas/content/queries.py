"""HTTP bodies of the language search queries endpoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class QueryCount(BaseModel):
    """One search query with the remaining stock found by it."""

    model_config = ConfigDict(frozen=True)

    query: str
    count: int
    in_file: bool


class QueriesResponse(BaseModel):
    """Full query list of one language."""

    model_config = ConfigDict(frozen=True)

    language: str
    queries: tuple[QueryCount, ...]
    unclassified: int
    total: int


class QueryCreateRequest(BaseModel):
    """Body of one query append request."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    language: str = Field(pattern=r"^[a-z]{2}$")
    query: str


class QueryCreateResponse(BaseModel):
    """Result of appending a query to the language file."""

    model_config = ConfigDict(frozen=True)

    language: str
    query: str
    created: bool
