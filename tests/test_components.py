import pytest

from common import config
from common.git import sanitize
from components import ALL_IMAGES, IMAGE_COMPONENTS
from components.controller.build import V2V_HELPER


def test_make_targets_per_component():
    assert {n: m.MAKE_TARGETS for n, m in IMAGE_COMPONENTS.items()} == {
        "controller": ["vjail-controller"],
        "ui": ["ui"],
        "sdk": ["build-vpwned"],
        "ai": ["vjailbreak-ai"],
    }


def test_images_per_component():
    assert {n: [i.key for i in m.IMAGES] for n, m in IMAGE_COMPONENTS.items()} == {
        "controller": ["v2v_helper", "controller"],
        "ui": ["ui"],
        "sdk": ["vpwned"],
        "ai": ["ai"],
    }


def test_all_images_order_and_names():
    assert [(i.key, i.local_repo, i.upstream_repo, i.quay_repo) for i in ALL_IMAGES] == [
        ("v2v_helper", "v2v-helper", "vjailbreak-v2v-helper", "proj_v2v"),
        ("controller", "vjailbreak-controller", "vjailbreak-controller", "proj_controller"),
        ("ui", "vjailbreak-ui", "vjailbreak-ui", "proj_ui"),
        ("vpwned", "vjailbreak-vpwned", "vjailbreak-vpwned", "proj_sdk"),
        ("ai", "vjailbreak-ai", "vjailbreak-ai", "proj_ai"),
    ]


def test_only_v2v_helper_is_built_as_personal_ref():
    assert [i.key for i in ALL_IMAGES if i.built_as_personal_ref] == ["v2v_helper"]


def test_image_refs(monkeypatch):
    monkeypatch.setattr(config, "QUAY_NAMESPACE", "ns")
    monkeypatch.setattr(config, "MIRROR_HOST", "127.0.0.1:5051")
    ui = IMAGE_COMPONENTS["ui"].IMAGES[0]
    assert ui.local_ref("t") == "local/platform9/vjailbreak-ui:t"
    assert ui.personal_ref("t") == "quay.io/ns/proj_ui:t"
    assert ui.mirror_ref("t") == "127.0.0.1:5051/ns/proj_ui:t"
    assert ui.upstream_ref_in_download_script() == "quay.io/platform9/vjailbreak-ui:$TAG"
    assert ui.personal_ref_in_download_script() == "quay.io/ns/proj_ui:$TAG"


def test_quay_repo_override(monkeypatch):
    monkeypatch.setitem(config._settings, "BOB_QUAY_REPO_V2V_HELPER", "my_v2v")
    assert V2V_HELPER.quay_repo == "my_v2v"


def test_controller_bakes_v2v_image(monkeypatch):
    monkeypatch.setattr(config, "QUAY_NAMESPACE", "ns")
    assert IMAGE_COMPONENTS["controller"].extra_env("t") == {"V2V_IMG": "quay.io/ns/proj_v2v:t"}
    for name in ("ui", "sdk", "ai"):
        assert IMAGE_COMPONENTS[name].extra_env("t") == {}


@pytest.mark.parametrize("branch,tag", [
    ("main", "main"),
    ("private/main/sarika/x", "private-main-sarika-x"),
    ("feat#1 two", "feat-1-two"),
    ("v1.2_rc-3", "v1.2_rc-3"),
])
def test_sanitize(branch, tag):
    assert sanitize(branch) == tag
