from tools.acceptance_health import (
    CheckResult,
    classify_health,
    command_contains_route,
    check_external_interface,
    check_data_plane,
    check_required_ports,
    check_runtime_environment,
    check_web_apis,
    check_static_hybrid_links,
    _find_mininet_host_pid,
    flow_output_has_bidirectional_flows,
    parse_ping_statistics,
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


def test_parse_ping_statistics_extracts_loss_and_rtt_ms():
    output = "\n".join([
        "3 packets transmitted, 3 received, 0% packet loss, time 2002ms",
        "rtt min/avg/max/mdev = 1.234/2.500/4.000/0.300 ms",
    ])

    stats = parse_ping_statistics(output)

    assert stats["transmitted"] == 3
    assert stats["received"] == 3
    assert stats["loss_percent"] == 0.0
    assert stats["avg_rtt_ms"] == 2.5
    assert stats["estimated_one_way_ms"] == 1.25


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


def test_check_required_ports_uses_configured_service_ports(monkeypatch):
    observed = []
    config = {
        "services": {
            "server_agent": {"port": 6101},
            "path_service": {"port": 8890},
            "web": {"port": 6010},
        },
        "controllers": {"ports": [6654, 6655]},
    }

    def fake_listening(port, host="127.0.0.1", timeout=0.3):
        observed.append(port)
        return True

    monkeypatch.setattr("tools.acceptance_health.tcp_port_listening", fake_listening)

    checks = check_required_ports(config)

    assert observed == [6101, 6010, 8890, 6654, 6655]
    assert [item.name for item in checks] == [
        "port_6101",
        "port_6010",
        "port_8890",
        "port_6654",
        "port_6655",
    ]


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


def test_check_data_plane_reports_measured_virtual_real_latency(monkeypatch):
    config = {
        "hybrid": {
            "gateway_ip": "10.0.0.254",
            "real_routes": ["192.168.103.0/24"],
            "external_link_ports": [{"dpid": 1, "port": 20}],
        },
        "validation": {
            "virtual_host_name": "h28",
            "virtual_host_ip": "10.0.0.28",
            "real_host_ip": "192.168.103.3",
            "expected_virtual_switch_dpid": 28,
        },
    }

    monkeypatch.setattr("tools.acceptance_health.fetch_web_json", lambda path: {"sessions": []})

    def fake_runner(command, timeout=8):
        if command == ["ps", "-eo", "pid=,args="]:
            return 0, "20370 bash --norc --noediting -is mininet:h28\n"
        if command == ["sudo", "mnexec", "-a", "20370", "ip", "route"]:
            return 0, "192.168.103.0/24 via 10.0.0.254 dev h28-eth0\n"
        if command == ["sudo", "mnexec", "-a", "20370", "ping", "-c", "3", "-W", "1", "192.168.103.3"]:
            return 0, "\n".join([
                "3 packets transmitted, 3 received, 0% packet loss, time 2002ms",
                "rtt min/avg/max/mdev = 1.000/2.000/3.000/0.100 ms",
            ])
        if command == ["sudo", "ovs-ofctl", "dump-flows", "s1"]:
            return 0, "nw_src=10.0.0.28,nw_dst=192.168.103.3 actions=output:20\n"
        if command == ["sudo", "ovs-ofctl", "dump-flows", "s28"]:
            return 0, "nw_src=192.168.103.3,nw_dst=10.0.0.28 actions=output:1\n"
        raise AssertionError(f"unexpected command: {command}")

    checks = check_data_plane(config, runner=fake_runner)

    latency = next(item for item in checks if item.name == "virtual_real_latency")
    assert latency.status == "pass"
    assert "avg_rtt_ms=2.000" in latency.details
    assert "estimated_one_way_ms=1.000" in latency.details


def test_check_runtime_environment_detects_json_exported_variables():
    config = {
        "runtime": {
            "route_mode": "hybrid",
            "drl_k_candidates": 5,
            "drl_inference_timeout_ms": 100,
            "drl_min_confidence": 0.5,
            "route_flow_idle_timeout": 120,
            "route_flow_hard_timeout": 0,
            "flow_install_barrier_timeout": 0.5,
        },
        "hybrid": {
            "external_switch": "s1",
            "external_port": 20,
            "external_link_ports": [{"dpid": 1, "port": 20}],
            "external_arp_allowed_prefixes": ["10.0.0.0/24"],
            "virtual_switch_dpid_max": 1000,
            "external_link_metrics": [],
            "static_links": [],
            "gateway_ip": "10.0.0.254",
            "gateway_mac": "02:00:00:00:fe:01",
            "real_routes": ["192.168.103.0/24"],
        },
        "load_test": {
            "flows": 20,
            "duration": 10,
            "parallel": 5,
            "seed": 1,
            "udp": False,
            "bandwidth": "10M",
        },
        "controllers": {"ports": [6654]},
        "validation": {"virtual_host_name": "h28"},
        "external_interface": "ens34",
        "traffic_classes": [
            {
                "name": "task_0",
                "port_start": 1,
                "port_end": 5000,
                "drl_type": 0,
                "route_policy": "min_delay",
                "flow_priority": 30,
                "drl_demand_kbps": 100,
                "drl_duration": 100,
            }
        ],
    }

    proc = {
        "123": "\0".join([
            "SERVER_AGENT_ROUTE_MODE=hybrid",
            "DRL_ROUTE_MODE=hybrid",
            "DRL_K_CANDIDATES=5",
            "DRL_INFERENCE_TIMEOUT_MS=100",
            "DRL_MIN_CONFIDENCE=0.5",
            "ROUTE_FLOW_IDLE_TIMEOUT=120",
            "ROUTE_FLOW_HARD_TIMEOUT=0",
            "FLOW_INSTALL_BARRIER_TIMEOUT=0.5",
            "SERVER_AGENT_BIND_IP=0.0.0.0",
            "SERVER_AGENT_IP=127.0.0.1",
            "SERVER_AGENT_PORT=6001",
            "SERVER_AGENT_LOG_LEVEL=INFO",
            "PATH_SERVICE_HOST=127.0.0.1",
            "PATH_SERVICE_PORT=8889",
            "PATH_SERVICE_TOPO=Military",
            "PATH_SERVICE_MODEL_DIR=model/Military_mininet",
            "WEB_PORT=6009",
            "EXTERNAL_LINK_PORTS=1:20",
            "EXTERNAL_SWITCH=s1",
            "EXTERNAL_PORT=20",
            "EXTERNAL_ARP_ALLOWED_PREFIXES=10.0.0.0/24",
            "VIRTUAL_SWITCH_DPID_MAX=1000",
            "EXTERNAL_LINK_METRICS_JSON=[]",
            "STATIC_HYBRID_LINKS_JSON=[]",
            'TRAFFIC_CLASSES_JSON=[{"name":"task_0","port_start":1,"port_end":5000,"drl_type":0,"route_policy":"min_delay","flow_priority":30,"drl_demand_kbps":100,"drl_duration":100}]',
            "LOG_DIR=logs",
            "REPORT_DIR=reports",
            "PATH_SERVICE_READY_TIMEOUT_SECONDS=90",
            "CONTROLLER_READY_TIMEOUT_SECONDS=90",
            "MININET_ROUTES_READY_TIMEOUT_SECONDS=240",
            "ALLOW_EXTERNAL_INTF_HAS_DEFAULT_ROUTE=0",
        ])
    }

    def fake_runner(command, timeout=8):
        if command == ["ps", "-eo", "pid=,args="]:
            return 0, "123 python3 server_agent.py hybrid\n"
        if command == ["cat", "/proc/123/environ"]:
            return 0, proc["123"]
        raise AssertionError(f"unexpected command: {command}")

    checks = check_runtime_environment(config, runner=fake_runner)

    assert [(item.name, item.status) for item in checks] == [("runtime_environment", "pass")]


def test_check_runtime_environment_detects_new_service_env_mismatch():
    config = {
        "runtime": {
            "route_mode": "hybrid",
            "drl_k_candidates": 5,
            "drl_inference_timeout_ms": 100,
            "drl_min_confidence": 0.5,
            "route_flow_idle_timeout": 120,
            "route_flow_hard_timeout": 0,
            "flow_install_barrier_timeout": 0.5,
        },
        "services": {
            "server_agent": {"bind_ip": "0.0.0.0", "port": 6101, "log_level": "INFO"},
            "path_service": {
                "host": "127.0.0.1",
                "port": 8890,
                "topo": "Military",
                "model_dir": "model/Military_mininet",
            },
            "web": {"port": 6010},
        },
        "startup": {
            "log_dir": "logs",
            "report_dir": "reports",
            "path_service_ready_timeout_seconds": 90,
            "controller_ready_timeout_seconds": 90,
            "mininet_routes_ready_timeout_seconds": 240,
        },
        "safety": {"allow_external_interface_default_route": False},
        "hybrid": {
            "external_switch": "s1",
            "external_port": 20,
            "external_link_ports": [{"dpid": 1, "port": 20}],
            "external_arp_allowed_prefixes": ["10.0.0.0/24"],
            "virtual_switch_dpid_max": 1000,
            "external_link_metrics": [],
            "static_links": [],
            "gateway_ip": "10.0.0.254",
            "gateway_mac": "02:00:00:00:fe:01",
            "real_routes": ["192.168.103.0/24"],
        },
        "load_test": {
            "flows": 20,
            "duration": 10,
            "parallel": 5,
            "seed": 1,
            "udp": False,
            "bandwidth": "10M",
        },
        "controllers": {"ports": [6654]},
        "validation": {"virtual_host_name": "h28"},
        "external_interface": "ens34",
        "traffic_classes": [
            {
                "name": "task_0",
                "port_start": 1,
                "port_end": 5000,
                "drl_type": 0,
                "route_policy": "min_delay",
                "flow_priority": 30,
                "drl_demand_kbps": 100,
                "drl_duration": 100,
            }
        ],
    }
    proc_env = "\0".join([
        "SERVER_AGENT_ROUTE_MODE=hybrid",
        "DRL_ROUTE_MODE=hybrid",
        "DRL_K_CANDIDATES=5",
        "DRL_INFERENCE_TIMEOUT_MS=100",
        "DRL_MIN_CONFIDENCE=0.5",
        "ROUTE_FLOW_IDLE_TIMEOUT=120",
        "ROUTE_FLOW_HARD_TIMEOUT=0",
        "FLOW_INSTALL_BARRIER_TIMEOUT=0.5",
        "EXTERNAL_LINK_PORTS=1:20",
        "EXTERNAL_SWITCH=s1",
        "EXTERNAL_PORT=20",
        "EXTERNAL_ARP_ALLOWED_PREFIXES=10.0.0.0/24",
        "VIRTUAL_SWITCH_DPID_MAX=1000",
        "EXTERNAL_LINK_METRICS_JSON=[]",
        "STATIC_HYBRID_LINKS_JSON=[]",
        'TRAFFIC_CLASSES_JSON=[{"name":"task_0","port_start":1,"port_end":5000,"drl_type":0,"route_policy":"min_delay","flow_priority":30,"drl_demand_kbps":100,"drl_duration":100}]',
        "SERVER_AGENT_PORT=6001",
    ])

    def fake_runner(command, timeout=8):
        if command == ["ps", "-eo", "pid=,args="]:
            return 0, "123 python3 server_agent.py hybrid\n"
        if command == ["cat", "/proc/123/environ"]:
            return 0, proc_env
        raise AssertionError(f"unexpected command: {command}")

    checks = check_runtime_environment(config, runner=fake_runner)

    assert checks[0].status == "fail"
    assert "SERVER_AGENT_PORT" in checks[0].details


def test_check_web_apis_uses_configured_web_port(monkeypatch):
    seen_urls = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(url, timeout=2):
        seen_urls.append(url)
        return FakeResponse()

    monkeypatch.setattr("tools.acceptance_health.urllib.request.urlopen", fake_urlopen)

    checks = check_web_apis({"services": {"web": {"port": 6010}}})

    assert seen_urls[0].startswith("http://127.0.0.1:6010/")
    assert all(item.status == "pass" for item in checks)


def test_check_static_hybrid_links_requires_configured_bidirectional_edges(monkeypatch):
    config = {
        "hybrid": {
            "static_links": [
                {
                    "src_dpid": 1,
                    "src_port": 20,
                    "dst_dpid": 128986965761,
                    "dst_port": 11,
                }
            ]
        }
    }
    graph = {
        "edges": [
            {"source": 1, "target": 128986965761, "data": {"src_port": 20}},
            {"source": 128986965761, "target": 1, "data": {"src_port": 11}},
        ]
    }
    monkeypatch.setattr("tools.acceptance_health.fetch_web_json", lambda path: graph)

    checks = check_static_hybrid_links(config)

    assert [(item.name, item.status) for item in checks] == [("static_hybrid_links", "pass")]


def test_check_static_hybrid_links_fails_when_reverse_port_is_missing(monkeypatch):
    config = {
        "hybrid": {
            "static_links": [
                {
                    "src_dpid": 1,
                    "src_port": 20,
                    "dst_dpid": 128986965761,
                    "dst_port": 11,
                }
            ]
        }
    }
    graph = {
        "edges": [
            {"source": 1, "target": 128986965761, "data": {"src_port": 20}},
            {"source": 128986965761, "target": 1, "data": {"src_port": 5}},
        ]
    }
    monkeypatch.setattr("tools.acceptance_health.fetch_web_json", lambda path: graph)

    checks = check_static_hybrid_links(config)

    assert checks[0].name == "static_hybrid_links"
    assert checks[0].status == "fail"
    assert "128986965761:11 -> 1" in checks[0].details
