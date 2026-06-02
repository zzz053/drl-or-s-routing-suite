import json

import pytest

from tools.acceptance_config import (
    AcceptanceConfigError,
    build_runtime_env,
    format_external_link_ports,
    format_traffic_classes,
    load_acceptance_config,
)


def write_config(tmp_path, data):
    path = tmp_path / "hybrid_acceptance.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def valid_config():
    return {
        "external_interface": "eno1",
        "controllers": {
            "ports": [6654, 6655, 6656, 6657, 6658, 6659, 6670],
        },
        "runtime": {
            "route_mode": "hybrid",
            "drl_k_candidates": 7,
            "drl_inference_timeout_ms": 150,
            "drl_min_confidence": 0.65,
            "route_flow_idle_timeout": 180,
            "route_flow_hard_timeout": 0,
            "flow_install_barrier_timeout": 0.75,
        },
        "hybrid": {
            "external_switch": "s1",
            "external_port": 20,
            "external_link_ports": [{"dpid": 1, "port": 20}],
            "gateway_ip": "10.0.0.254",
            "gateway_mac": "02:00:00:00:fe:01",
            "real_routes": ["192.168.103.0/24"],
            "external_arp_allowed_prefixes": ["10.0.0.0/24", "192.168.103.0/24"],
            "virtual_switch_dpid_max": 1000,
            "external_link_metrics": [
                {
                    "dpid": 1,
                    "port": 20,
                    "delay_ms": 2.5,
                    "bandwidth_mbps": 800,
                    "loss_percent": 0.0,
                    "source": "configured",
                }
            ],
            "static_links": [
                {
                    "src_dpid": 1,
                    "src_port": 20,
                    "dst_dpid": 128986965761,
                    "dst_port": 11,
                    "delay_ms": 2.5,
                    "bandwidth_mbps": 800,
                    "loss_percent": 0.0,
                    "source": "configured_static_link",
                }
            ],
        },
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
            },
            {
                "name": "task_1",
                "port_start": 5001,
                "port_end": 10000,
                "drl_type": 1,
                "route_policy": "max_bandwidth",
                "flow_priority": 20,
                "drl_demand_kbps": 1500,
                "drl_duration": 100,
            },
            {
                "name": "task_2",
                "port_start": 10001,
                "port_end": 65535,
                "drl_type": 2,
                "route_policy": "hybrid",
                "flow_priority": 10,
                "drl_demand_kbps": 1500,
                "drl_duration": 100,
            },
        ],
        "load_test": {
            "flows": 30,
            "duration": 15,
            "parallel": 6,
            "seed": 3,
            "udp": False,
            "bandwidth": "20M",
        },
        "validation": {
            "virtual_host_name": "h28",
            "virtual_host_ip": "10.0.0.28",
            "real_host_ip": "192.168.103.3",
            "expected_real_switch_dpid": 128986965761,
        },
    }


def test_load_acceptance_config_validates_and_preserves_required_values(tmp_path):
    cfg = load_acceptance_config(write_config(tmp_path, valid_config()))

    assert cfg["external_interface"] == "eno1"
    assert cfg["controllers"]["ports"] == [6654, 6655, 6656, 6657, 6658, 6659, 6670]
    assert cfg["runtime"]["route_mode"] == "hybrid"
    assert cfg["hybrid"]["external_switch"] == "s1"
    assert cfg["hybrid"]["external_port"] == 20
    assert cfg["hybrid"]["external_link_metrics"][0]["delay_ms"] == 2.5
    assert cfg["hybrid"]["static_links"][0]["dst_dpid"] == 128986965761
    assert cfg["hybrid"]["static_links"][0]["dst_port"] == 11
    assert cfg["traffic_classes"][1]["name"] == "task_1"
    assert cfg["traffic_classes"][1]["drl_type"] == 1
    assert cfg["traffic_classes"][1]["route_policy"] == "max_bandwidth"
    assert cfg["load_test"]["flows"] == 30
    assert "forbidden_ports" not in cfg["controllers"]
    assert cfg["validation"]["real_host_ip"] == "192.168.103.3"


def test_load_acceptance_config_ignores_legacy_forbidden_ports(tmp_path):
    data = valid_config()
    data["controllers"]["forbidden_ports"] = [6671]

    cfg = load_acceptance_config(write_config(tmp_path, data))

    assert "forbidden_ports" not in cfg["controllers"]


def test_load_acceptance_config_does_not_guess_external_interface(tmp_path):
    data = valid_config()
    data.pop("external_interface")

    with pytest.raises(AcceptanceConfigError, match="external_interface"):
        load_acceptance_config(write_config(tmp_path, data))


def test_format_external_link_ports_supports_multiple_pairs():
    cfg = valid_config()
    cfg["hybrid"]["external_link_ports"] = [
        {"dpid": 1, "port": 20},
        {"dpid": 42, "port": 7},
    ]

    assert format_external_link_ports(cfg) == "1:20,42:7"


def test_build_runtime_env_maps_config_to_existing_variables():
    env = build_runtime_env(valid_config())

    assert env["EXTERNAL_INTF"] == "eno1"
    assert env["EXTERNAL_SWITCH"] == "s1"
    assert env["EXTERNAL_PORT"] == "20"
    assert env["EXTERNAL_LINK_PORTS"] == "1:20"
    assert env["CONTROLLER_PORTS"] == "6654 6655 6656 6657 6658 6659 6670"
    assert env["SERVER_AGENT_ROUTE_MODE"] == "hybrid"
    assert env["DRL_ROUTE_MODE"] == "hybrid"
    assert env["DRL_K_CANDIDATES"] == "7"
    assert env["DRL_INFERENCE_TIMEOUT_MS"] == "150"
    assert env["DRL_MIN_CONFIDENCE"] == "0.65"
    assert env["ROUTE_FLOW_IDLE_TIMEOUT"] == "180"
    assert env["ROUTE_FLOW_HARD_TIMEOUT"] == "0"
    assert env["FLOW_INSTALL_BARRIER_TIMEOUT"] == "0.75"
    assert env["EXTERNAL_ARP_ALLOWED_PREFIXES"] == "10.0.0.0/24,192.168.103.0/24"
    assert env["VIRTUAL_SWITCH_DPID_MAX"] == "1000"
    assert '"delay_ms":2.5' in env["EXTERNAL_LINK_METRICS_JSON"]
    assert '"src_dpid":1' in env["STATIC_HYBRID_LINKS_JSON"]
    assert '"dst_dpid":128986965761' in env["STATIC_HYBRID_LINKS_JSON"]
    assert '"name":"task_1"' in env["TRAFFIC_CLASSES_JSON"]
    assert '"drl_type":1' in env["TRAFFIC_CLASSES_JSON"]
    assert '"drl_demand_kbps":1500' in env["TRAFFIC_CLASSES_JSON"]
    assert env["LOAD_TEST_FLOWS"] == "30"
    assert env["LOAD_TEST_PARALLEL"] == "6"
    assert env["VALIDATION_VIRTUAL_HOST_NAME"] == "h28"
    assert env["HYBRID_GATEWAY_IP"] == "10.0.0.254"
    assert env["HYBRID_GATEWAY_MAC"] == "02:00:00:00:fe:01"
    assert env["HYBRID_REAL_ROUTES"] == "192.168.103.0/24"


def test_format_traffic_classes_uses_default_three_class_drl_mapping_when_missing(tmp_path):
    data = valid_config()
    data.pop("traffic_classes")

    cfg = load_acceptance_config(write_config(tmp_path, data))

    assert cfg["traffic_classes"] == [
        {
            "name": "task_0",
            "port_start": 1,
            "port_end": 5000,
            "drl_type": 0,
            "route_policy": "min_delay",
            "flow_priority": 30,
            "drl_demand_kbps": 100,
            "drl_duration": 100,
        },
        {
            "name": "task_1",
            "port_start": 5001,
            "port_end": 10000,
            "drl_type": 1,
            "route_policy": "max_bandwidth",
            "flow_priority": 20,
            "drl_demand_kbps": 1500,
            "drl_duration": 100,
        },
        {
            "name": "task_2",
            "port_start": 10001,
            "port_end": 65535,
            "drl_type": 2,
            "route_policy": "hybrid",
            "flow_priority": 10,
            "drl_demand_kbps": 1500,
            "drl_duration": 100,
        },
    ]
    assert '"name":"task_2"' in format_traffic_classes(cfg)


def test_load_acceptance_config_rejects_invalid_drl_type(tmp_path):
    data = valid_config()
    data["traffic_classes"][0]["drl_type"] = 3

    with pytest.raises(AcceptanceConfigError, match="traffic_classes\\[0\\].drl_type"):
        load_acceptance_config(write_config(tmp_path, data))


def test_load_acceptance_config_rejects_mismatched_external_switch_and_link_port(tmp_path):
    data = valid_config()
    data["hybrid"]["external_switch"] = "s2"

    with pytest.raises(AcceptanceConfigError, match="external_switch"):
        load_acceptance_config(write_config(tmp_path, data))


def test_environment_specific_acceptance_configs_are_valid():
    vm_cfg = load_acceptance_config("config/hybrid_acceptance.vm.json")
    server_cfg = load_acceptance_config("config/hybrid_acceptance.server.json")

    assert vm_cfg["external_interface"] == "ens34"
    assert server_cfg["external_interface"] != vm_cfg["external_interface"]
    assert format_external_link_ports(vm_cfg) == "1:20"
    assert format_external_link_ports(server_cfg) == "1:20"
    assert vm_cfg["hybrid"]["static_links"][0]["dst_dpid"] == 128986965761
    assert server_cfg["hybrid"]["static_links"][0]["dst_port"] == 11
