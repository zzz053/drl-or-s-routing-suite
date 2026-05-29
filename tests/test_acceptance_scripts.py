from pathlib import Path
import subprocess
import sys


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
    assert "sudo_cmd -E" in text
    assert "testbed/creat_test_topo.py" in text
    assert "start_controllers_test.py start -n" in text
    assert "start_controllers_test.py stop" in text
    assert "sudo_cmd mn -c" in text


def test_acceptance_script_supports_noninteractive_sudo_password():
    text = (ROOT / "acceptance.sh").read_text(encoding="utf-8")

    assert "SUDO_PASSWORD" in text
    assert "sudo -S" in text


def test_acceptance_tool_scripts_are_directly_executable():
    for script in [
        "tools/acceptance_health.py",
        "tools/generate_acceptance_report.py",
    ]:
        result = subprocess.run(
            [sys.executable, script, "--help"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_vm_acceptance_doc_mentions_static_boundary_and_lldp_limitation():
    text = (ROOT / "docs" / "vm-acceptance-deployment.md").read_text(encoding="utf-8")

    assert "config/hybrid_acceptance.json" in text
    assert "LLDP" in text
    assert "静态虚实边界" in text
    assert "./acceptance.sh start" in text
    assert "./acceptance.sh health" in text
    assert "./acceptance.sh report" in text
