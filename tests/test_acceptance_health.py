from tools.acceptance_health import (
    CheckResult,
    classify_health,
    command_contains_route,
    check_external_interface,
    _find_mininet_host_pid,
    flow_output_has_bidirectional_flows,
    switch_names_for_flow_checks,
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


def test_flow_output_accepts_non_default_idle_timeout_when_bidirectional_ips_match():
    forward = "idle_timeout=30,ip,nw_src=10.0.0.10,nw_dst=172.16.0.3 actions=output:1\n"
    reverse = "idle_timeout=30,ip,nw_src=172.16.0.3,nw_dst=10.0.0.10 actions=output:2\n"

    assert flow_output_has_bidirectional_flows(
        [forward, reverse],
        virtual_ip="10.0.0.10",
        real_ip="172.16.0.3",
    )


def test_switch_names_for_flow_checks_uses_config_and_route_session_without_lab_hardcodes():
    config = {
        "hybrid": {"external_link_ports": [{"dpid": 1, "port": 20}]},
        "validation": {"expected_virtual_switch_dpid": 28},
    }
    route_sessions = {
        "sessions": [
            {"switch_path": [28, 6, 1, 128985343745]},
        ]
    }

    assert switch_names_for_flow_checks(config, route_sessions) == ["s1", "s6", "s28"]


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
    assert calls[0][0][:4] == ["sudo", "-S", "-p", ""]
    assert calls[0][1]["input"] == "h\n"


def test_check_external_interface_fails_when_configured_interface_is_not_attached_to_ovs():
    config = {
        "external_interface": "eno1",
        "hybrid": {"external_link_ports": [{"dpid": 1, "port": 20}]},
    }

    def fake_runner(command, timeout=8):
        if command == ["ip", "link", "show", "eno1"]:
            return 0, "2: eno1: <BROADCAST> mtu 1500\n"
        if command == ["ip", "route", "show", "default"]:
            return 0, "default via 192.168.172.2 dev ens33 proto dhcp metric 100\n"
        if command == ["sudo", "ovs-vsctl", "port-to-br", "eno1"]:
            return 1, "ovs-vsctl: no port named eno1\n"
        raise AssertionError(f"unexpected command: {command}")

    checks = check_external_interface(config, runner=fake_runner)

    assert any(item.name == "external_interface_ovs_bridge" and item.status == "fail" for item in checks)


def test_check_external_interface_passes_when_configured_interface_matches_static_boundary():
    config = {
        "external_interface": "ens34",
        "hybrid": {"external_link_ports": [{"dpid": 1, "port": 20}]},
    }

    def fake_runner(command, timeout=8):
        if command == ["ip", "link", "show", "ens34"]:
            return 0, "3: ens34: <BROADCAST,UP,LOWER_UP> mtu 1500\n"
        if command == ["ip", "route", "show", "default"]:
            return 0, "default via 192.168.172.2 dev ens33 proto dhcp metric 100\n"
        if command == ["sudo", "ovs-vsctl", "port-to-br", "ens34"]:
            return 0, "s1\n"
        if command == ["sudo", "ovs-vsctl", "get", "Interface", "ens34", "ofport"]:
            return 0, "20\n"
        raise AssertionError(f"unexpected command: {command}")

    checks = check_external_interface(config, runner=fake_runner)

    assert [(item.name, item.status) for item in checks] == [
        ("external_interface_exists", "pass"),
        ("external_interface_default_route", "pass"),
        ("external_interface_ovs_bridge", "pass"),
        ("external_interface_ofport", "pass"),
    ]
