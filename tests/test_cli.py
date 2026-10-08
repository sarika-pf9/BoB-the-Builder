import pytest

import cli


@pytest.mark.parametrize("component", ["controller", "ui", "sdk", "ai", "qcow2"])
def test_accepts_every_component(component):
    args = cli.build_parser().parse_args([component, "main"])
    assert (args.component, args.branch, args.push) == (component, "main", False)


def test_push_flag():
    assert cli.build_parser().parse_args(["ui", "main", "--push"]).push is True


def test_rejects_unknown_component():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["v2v", "main"])


def test_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "build_component", lambda *a, **kw: calls.append(("component", a, kw)))
    monkeypatch.setattr(cli.qcow2, "build_qcow2", lambda branch: calls.append(("qcow2", branch)))
    cli.main(["sdk", "dev", "--push"])
    cli.main(["qcow2", "dev"])
    assert calls == [("component", ("sdk", "dev"), {"push": True}), ("qcow2", "dev")]
