from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_acceptance_script_exposes_required_commands():
    text = (ROOT / "acceptance.sh").read_text(encoding="utf-8")

    assert 'case "$COMMAND" in' in text
    for command in ["start)", "stop)", "health)", "report)"]:
        assert command in text


def test_acceptance_script_uses_config_driven_environment_and_sudo_boundary():
    text = (ROOT / "acceptance.sh").read_text(encoding="utf-8")

    assert "tools/acceptance_config.py" in text
    assert "--shell-env" in text
    assert "sudo -E" in text
    assert "testbed/creat_test_topo.py" in text
    assert "start_controllers_test.py start -n" in text
    assert "start_controllers_test.py stop" in text
    assert "sudo mn -c" in text


def test_vm_acceptance_doc_mentions_static_boundary_and_lldp_limitation():
    text = (ROOT / "docs" / "vm-acceptance-deployment.md").read_text(encoding="utf-8")

    assert "config/hybrid_acceptance.json" in text
    assert "LLDP" in text
    assert "静态虚实边界" in text
    assert "./acceptance.sh start" in text
    assert "./acceptance.sh health" in text
    assert "./acceptance.sh report" in text
