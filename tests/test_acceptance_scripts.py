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
    assert "sudo -S -v" in text
    assert 'sudo "$@"' in text


def test_shell_scripts_are_forced_to_lf_for_linux_deployment():
    text = (ROOT / ".gitattributes").read_text(encoding="utf-8")

    assert "*.sh text eol=lf" in text


def test_acceptance_script_uses_separate_mininet_python():
    text = (ROOT / "acceptance.sh").read_text(encoding="utf-8")

    assert "MININET_PYTHON" in text
    assert '"$MININET_PYTHON" -u testbed/creat_test_topo.py' in text


def test_acceptance_stop_cleans_stale_project_processes():
    text = (ROOT / "acceptance.sh").read_text(encoding="utf-8")

    assert 'pkill -f "server_agent.py"' in text
    assert 'pkill -f "drl-or-s/path_service.py"' in text
    assert 'pkill -f "testbed/creat_test_topo.py"' in text


def test_acceptance_start_waits_for_path_service_and_clears_stale_logs():
    text = (ROOT / "acceptance.sh").read_text(encoding="utf-8")

    assert "wait_for_port" in text
    assert "wait_for_port 127.0.0.1 8889" in text
    assert "rm -f logs/*.log" in text


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
