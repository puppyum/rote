import os
from pathlib import Path

import pytest

import rote
from rote import session


@pytest.fixture(autouse=True)
def _isolated(tmp_path):
    cache_dir = tmp_path / ".rote"
    cache_dir.mkdir()
    os.environ["ROTE_DIR"] = str(cache_dir)
    rote.configure(cache_dir=cache_dir, telemetry=False, min_duration_s=0.0)
    session._reset_for_testing()
    yield cache_dir
    session._reset_for_testing()
