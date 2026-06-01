from tools.acceptance_health import (
    CheckResult,
    classify_health,
    command_contains_route,
    _find_mininet_host_pid,
    flow_output_has_bidirectional_flows,
    run_command,
)


def test_classify_health_passes_when_control_and_data_checks_pass():
    control = [CheckResult("server_agent", "pass", "6001 listening")]
    data = [CheckResult("ping", "pass", "verification ping passed")]

    assert classify_health(control, data) == "pass"


def test_classify_health_fails_when_control_check_fails():
    control = [CheckResult("ryu_6654", "fail", "port missing")]
    data = [CheckResult("ping", "pass", "verification ping passed")]

    assert classify_health(control, data) == "fail"


def test_classify_health_returns_risk_when_data_plane_unavailable():
    control = [CheckResult("server_agent", "pass", "6001 listening")]
    data = [CheckResult("mininet_h28", "risk", "host namespace unavailable")]

    assert classify_health(control, data) == "risk"


def test_command_contains_route_matches_real_subnet_gateway():
    output = "192.168.103.0/24 via 10.0.0.254 dev h28-eth0\n"

    assert command_contains_route(output, "192.168.103.0/24", "10.0.0.254")


def test_find_mininet_host_pid_ignores_probe_command_and_selects_namespace_shell():
    def fake_runner(command, timeout=3):
        assert command[:3] == ["ps", "-eo", "pid=,args="]
        return 0, "\n".join([
            "24790 bash -c pgrep -f mininet:h28",
            "20370 bash --norc --noediting -is mininet:h28",
        ])

    pid, _ = _find_mininet_host_pid("h28", runner=fake_runner)

    assert pid == "20370"


def test_flow_output_has_bidirectional_idle_timeout_flows():
    s28 = "idle_timeout=120,ip,nw_src=10.0.0.28,nw_dst=192.168.103.3 actions=output:1\n"
    s1 = "idle_timeout=120,ip,nw_src=192.168.103.3,nw_dst=10.0.0.28 actions=output:2\n"

    assert flow_output_has_bidirectional_flows(
        [s28, s1],
        virtual_ip="10.0.0.28",
        real_ip="192.168.103.3",
    )


def test_run_command_supplies_sudo_password_without_changing_non_sudo(monkeypatch):
    calls = []

    class Completed:
        returncode = 0
        stdout = "ok"

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setenv("SUDO_PASSWORD", "h")
    monkeypatch.setattr("tools.acceptance_health.subprocess.run", fake_run)

    code, output = run_command(["sudo", "mnexec", "-a", "123", "ip", "route"])

    assert code == 0
    assert output == "ok"
    assert calls[0][0][:2] == ["sudo", "-S"]
    assert calls[0][1]["input"] == "h\n"
