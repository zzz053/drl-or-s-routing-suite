import json

import pytest

from tools.acceptance_config import (
    AcceptanceConfigError,
    build_runtime_env,
    format_external_link_ports,
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
        "hybrid": {
            "external_link_ports": [{"dpid": 1, "port": 20}],
            "gateway_ip": "10.0.0.254",
            "gateway_mac": "02:00:00:00:fe:01",
            "real_routes": ["192.168.103.0/24"],
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
    assert env["EXTERNAL_LINK_PORTS"] == "1:20"
    assert env["CONTROLLER_PORTS"] == "6654 6655 6656 6657 6658 6659 6670"
    assert env["VALIDATION_VIRTUAL_HOST_NAME"] == "h28"
    assert env["HYBRID_GATEWAY_IP"] == "10.0.0.254"
    assert env["HYBRID_GATEWAY_MAC"] == "02:00:00:00:fe:01"
    assert env["HYBRID_REAL_ROUTES"] == "192.168.103.0/24"


def test_environment_specific_acceptance_configs_are_valid():
    vm_cfg = load_acceptance_config("config/hybrid_acceptance.vm.json")
    server_cfg = load_acceptance_config("config/hybrid_acceptance.server.json")

    assert vm_cfg["external_interface"] == "ens34"
    assert server_cfg["external_interface"] != vm_cfg["external_interface"]
    assert format_external_link_ports(vm_cfg) == "1:20"
    assert format_external_link_ports(server_cfg) == "1:20"
