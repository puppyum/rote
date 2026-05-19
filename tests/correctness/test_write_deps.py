"""§3.3.1 file-write dependencies — cache must re-run when its outputs vanish."""

from __future__ import annotations

import pytest

import rote


@pytest.fixture(autouse=True)
def _isolated(tmp_path):
    rote.configure(cache_dir=tmp_path / ".rote", telemetry=False, min_duration_s=0.0)
    rote.clear()


def test_deleted_write_output_triggers_recompute(tmp_path):
    """A cached call that writes data.txt must re-run if data.txt is deleted."""
    out_path = tmp_path / "data.txt"

    @rote.cache
    def produce():
        out_path.write_text("hello")
        return out_path.read_text()

    assert produce() == "hello"
    assert produce() == "hello"  # cache hit

    # Delete the output. Next call must re-run to re-create it.
    out_path.unlink()
    assert produce() == "hello"
    assert out_path.exists()


def test_edited_write_output_triggers_recompute(tmp_path):
    """If someone manually edits the output file, cache must re-run."""
    out_path = tmp_path / "data.txt"

    @rote.cache
    def produce():
        out_path.write_text("v1")
        return "v1"

    produce()
    # Mess with the output.
    out_path.write_text("TAMPERED")
    produce()
    assert out_path.read_text() == "v1", "function should have re-created clean output"


def test_unchanged_write_output_still_hits(tmp_path):
    """If the file is untouched between calls, cache should hit."""
    out_path = tmp_path / "data.txt"

    @rote.cache
    def produce():
        out_path.write_text("hello")
        return "ok"

    produce()
    produce()
    produce()
    assert rote.stats()["hits"] >= 2


def test_write_deps_persisted_to_store(tmp_path):
    """Verify the Entry actually records the write paths in SQLite."""
    out_path = tmp_path / "data.txt"

    @rote.cache
    def produce():
        out_path.write_text("x")
        return None

    produce()
    from rote.session import _get_session

    entries = _get_session().ensure_store().all_entries()
    assert any(str(out_path) in e.file_write_dependencies for e in entries)
