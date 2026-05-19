from pathlib import Path

import pytest

from rote import Config, configure, get_config


def test_config_defaults():
    c = Config()
    assert c.min_duration_s == 1.0
    assert isinstance(c.cache_dir, Path)


def test_configure_updates_global(tmp_path):
    new_dir = tmp_path / "alt"
    configure(cache_dir=new_dir, min_duration_s=0.5)
    cfg = get_config()
    assert cfg.cache_dir == new_dir
    assert cfg.min_duration_s == 0.5


def test_configure_rejects_unknown():
    with pytest.raises(AttributeError):
        configure(banana=True)
