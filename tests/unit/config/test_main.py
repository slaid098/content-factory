from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI


def test_main_exposes_fastapi_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))

    import main

    assert isinstance(main.app, FastAPI)
