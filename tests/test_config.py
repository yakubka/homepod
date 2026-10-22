import os
import subprocess
import sys

import pytest

from services.core import config


@pytest.fixture
def layered(tmp_path, monkeypatch):
    default, local, saved = tmp_path / "default.yaml", tmp_path / "local.yaml", tmp_path / "device_config.json"
    monkeypatch.setattr(config, "DEFAULT_FILE", str(default))
    monkeypatch.setattr(config, "LOCAL_FILE", str(local))
    monkeypatch.setattr(config, "PATH", str(saved))
    monkeypatch.delenv("VESTA_LIGHT_HOLD", raising=False)
    return default, local, saved


def test_each_layer_overrides_the_one_before(layered, monkeypatch):
    default, local, saved = layered
    assert config.load()["light.hold"] == 30.0

    default.write_text("light:\n  hold: 40\n")
    assert config.load()["light.hold"] == 40.0

    local.write_text("light.hold: 50\n")
    assert config.load()["light.hold"] == 50.0

    saved.write_text('{"light.hold": 55}')
    assert config.load()["light.hold"] == 55.0

    monkeypatch.setenv("VESTA_LIGHT_HOLD", "60")
    assert config.load()["light.hold"] == 60.0


def test_environment_values_are_parsed_to_the_default_type(layered, monkeypatch):
    monkeypatch.setenv("VESTA_PRESENTATION_ZONES", "[center, left]")
    monkeypatch.setenv("VESTA_CLIMATE_ENABLED", "true")
    cfg = config.load()
    assert cfg["presentation.zones"] == ["center", "left"] and cfg["climate.enabled"] is True


def test_a_wrong_type_is_rejected_with_its_source(layered, monkeypatch):
    monkeypatch.setenv("VESTA_API_PORT", "not-a-port")
    with pytest.raises(config.ConfigError, match="api_port from environment"):
        config.load()


def test_unknown_keys_are_ignored(layered):
    layered[1].write_text("no.such.key: 1\n")
    assert "no.such.key" not in config.load()


def test_update_persists_only_the_changed_key(layered):
    ok, _ = config.update("light.hold", 45)
    assert ok and config.load()["light.hold"] == 45.0
    assert layered[2].read_text().count(":") == 1


def test_an_unknown_mode_stops_the_process():
    env = {**os.environ, "VESTA_MODE": "laptop"}
    out = subprocess.run([sys.executable, "-c", "import services.core.config"], capture_output=True, text=True, env=env, cwd=config.ROOT)
    assert out.returncode != 0 and "VESTA_MODE must be one of replay, sim, real" in out.stderr
