import pytest

from common.config import load_settings, parse_env_file


@pytest.mark.parametrize("text,expected", [
    ("A=1", {"A": "1"}),
    ("  A = 1  ", {"A": "1"}),
    ("export A=1", {"A": "1"}),
    ('A="quoted value"', {"A": "quoted value"}),
    ("A='single'", {"A": "single"}),
    ("A=", {"A": ""}),
    ("# comment\n\nA=1\n", {"A": "1"}),
    ("not a pair", {}),
    ("A=x=y", {"A": "x=y"}),
    ('A="unbalanced', {"A": '"unbalanced'}),
])
def test_parse_env_file(text, expected):
    assert parse_env_file(text) == expected


def test_env_overrides_file(tmp_path):
    cfg = tmp_path / "bob.env"
    cfg.write_text("BOB_QUAY_NAMESPACE=fromfile\nBOB_MIRROR_PORT=6000\n")
    settings = load_settings(cfg, environ={"BOB_QUAY_NAMESPACE": "fromenv", "HOME": "/x"})
    assert settings["BOB_QUAY_NAMESPACE"] == "fromenv"
    assert settings["BOB_MIRROR_PORT"] == "6000"
    assert "HOME" not in settings


def test_missing_file_is_empty(tmp_path):
    assert load_settings(tmp_path / "nope.env", environ={}) == {}


def test_bob_config_env_selects_file(tmp_path):
    cfg = tmp_path / "other.env"
    cfg.write_text("BOB_VJB_REPO_DIR=/srv/vjb\n")
    settings = load_settings(environ={"BOB_CONFIG": str(cfg)})
    assert settings["BOB_VJB_REPO_DIR"] == "/srv/vjb"
