import pytest

from common import config, quay
from components import ALL_IMAGES
from components.qcow2 import mirror
from components.qcow2.download_images import patch_download_images_sh

UPSTREAM_SCRIPT = """\
v2v_helper="quay.io/platform9/vjailbreak-v2v-helper:$TAG"
controller="quay.io/platform9/vjailbreak-controller:$TAG"
ui="quay.io/platform9/vjailbreak-ui:$TAG"
vpwned="quay.io/platform9/vjailbreak-vpwned:$TAG"
ai="quay.io/platform9/vjailbreak-ai:$TAG"
other="quay.io/prometheus/prometheus:v3"
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "REPO_DIR", tmp_path)
    monkeypatch.setattr(config, "QUAY_NAMESPACE", "ns")
    script = tmp_path / "image_builder" / "scripts" / "download_images.sh"
    script.parent.mkdir(parents=True)
    script.write_text(UPSTREAM_SCRIPT)
    return script


def test_patch_rewrites_all_five(repo):
    patch_download_images_sh(ALL_IMAGES)
    text = repo.read_text()
    assert "quay.io/platform9/vjailbreak" not in text
    for repo_name in ("proj_v2v", "proj_controller", "proj_ui", "proj_sdk", "proj_ai"):
        assert f"quay.io/ns/{repo_name}:$TAG" in text
    assert 'other="quay.io/prometheus/prometheus:v3"' in text


def test_patch_fails_loudly_when_upstream_changed(repo):
    repo.write_text(UPSTREAM_SCRIPT.replace("vjailbreak-ui", "vjailbreak-web"))
    with pytest.raises(AssertionError, match="vjailbreak-ui"):
        patch_download_images_sh(ALL_IMAGES)


@pytest.fixture
def recorded(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "QUAY_NAMESPACE", "ns")
    monkeypatch.setattr(config, "MIRROR_HOST", "127.0.0.1:5051")
    monkeypatch.setattr(quay, "run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(mirror, "run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(mirror, "ensure_mirror_ready", lambda: None)
    return calls


def test_push_skips_retag_for_v2v_helper(recorded):
    quay.push_to_personal_quay("t", ALL_IMAGES[:2])
    assert recorded == [
        ["docker", "push", "quay.io/ns/proj_v2v:t"],
        ["docker", "tag", "local/platform9/vjailbreak-controller:t", "quay.io/ns/proj_controller:t"],
        ["docker", "push", "quay.io/ns/proj_controller:t"],
    ]


def test_mirror_publish(recorded):
    mirror.publish_local_images_to_mirror("t", ALL_IMAGES[:2])
    assert recorded == [
        ["docker", "tag", "quay.io/ns/proj_v2v:t", "127.0.0.1:5051/ns/proj_v2v:t"],
        ["docker", "push", "127.0.0.1:5051/ns/proj_v2v:t"],
        ["docker", "tag", "local/platform9/vjailbreak-controller:t", "quay.io/ns/proj_controller:t"],
        ["docker", "tag", "local/platform9/vjailbreak-controller:t", "127.0.0.1:5051/ns/proj_controller:t"],
        ["docker", "push", "127.0.0.1:5051/ns/proj_controller:t"],
    ]


def test_require_quay_namespace(monkeypatch):
    monkeypatch.setattr(config, "QUAY_NAMESPACE", "")
    with pytest.raises(SystemExit):
        quay.require_quay_namespace()
