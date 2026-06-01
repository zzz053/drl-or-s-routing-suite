#!/usr/bin/env python3
"""Acceptance configuration parsing for hybrid Mininet/real-switch deployment."""

import argparse
import json
from pathlib import Path
import shlex
import sys


DEFAULT_CONTROLLERS = {
    "ports": [6654, 6655, 6656, 6657, 6658, 6659, 6670],
}
DEFAULT_HYBRID = {
    "gateway_ip": "10.0.0.254",
    "gateway_mac": "02:00:00:00:fe:01",
}


class AcceptanceConfigError(ValueError):
    """Raised when the acceptance config is missing or invalid."""


def _require_mapping(value, name):
    if not isinstance(value, dict):
        raise AcceptanceConfigError(f"{name} must be an object")
    return value


def _require_string(value, name):
    if not isinstance(value, str) or not value.strip():
        raise AcceptanceConfigError(f"{name} is required")
    return value.strip()


def _require_int(value, name):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise AcceptanceConfigError(f"{name} must be an integer")


def _require_string_list(value, name):
    if not isinstance(value, list) or not value:
        raise AcceptanceConfigError(f"{name} must be a non-empty list")
    out = []
    for idx, item in enumerate(value):
        out.append(_require_string(item, f"{name}[{idx}]"))
    return out


def _normalize_external_link_ports(value):
    if not isinstance(value, list) or not value:
        raise AcceptanceConfigError("hybrid.external_link_ports must be a non-empty list")
    pairs = []
    for idx, item in enumerate(value):
        item = _require_mapping(item, f"hybrid.external_link_ports[{idx}]")
        pairs.append({
            "dpid": _require_int(item.get("dpid"), f"hybrid.external_link_ports[{idx}].dpid"),
            "port": _require_int(item.get("port"), f"hybrid.external_link_ports[{idx}].port"),
        })
    return pairs


def _load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise AcceptanceConfigError(f"config file not found: {path}")
    except json.JSONDecodeError as exc:
        raise AcceptanceConfigError(
            f"invalid JSON in {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}"
        )


def load_acceptance_config(path="config/hybrid_acceptance.json"):
    path = Path(path)
    raw = _require_mapping(_load_json(path), "config")

    config = dict(raw)
    config["external_interface"] = _require_string(
        raw.get("external_interface"),
        "external_interface",
    )

    controllers = dict(DEFAULT_CONTROLLERS)
    controllers.update(_require_mapping(raw.get("controllers", {}), "controllers"))
    controllers["ports"] = [
        _require_int(port, f"controllers.ports[{idx}]")
        for idx, port in enumerate(controllers.get("ports", []))
    ]
    controllers.pop("forbidden_ports", None)
    if not controllers["ports"]:
        raise AcceptanceConfigError("controllers.ports must be a non-empty list")
    config["controllers"] = controllers

    hybrid = dict(DEFAULT_HYBRID)
    hybrid.update(_require_mapping(raw.get("hybrid"), "hybrid"))
    hybrid["external_link_ports"] = _normalize_external_link_ports(
        hybrid.get("external_link_ports")
    )
    hybrid["gateway_ip"] = _require_string(hybrid.get("gateway_ip"), "hybrid.gateway_ip")
    hybrid["gateway_mac"] = _require_string(hybrid.get("gateway_mac"), "hybrid.gateway_mac")
    hybrid["real_routes"] = _require_string_list(hybrid.get("real_routes"), "hybrid.real_routes")
    config["hybrid"] = hybrid

    validation = dict(_require_mapping(raw.get("validation"), "validation"))
    validation["virtual_host_name"] = _require_string(
        validation.get("virtual_host_name"),
        "validation.virtual_host_name",
    )
    validation["virtual_host_ip"] = _require_string(
        validation.get("virtual_host_ip"),
        "validation.virtual_host_ip",
    )
    validation["real_host_ip"] = _require_string(
        validation.get("real_host_ip"),
        "validation.real_host_ip",
    )
    if validation.get("expected_real_switch_dpid") not in (None, ""):
        validation["expected_real_switch_dpid"] = _require_int(
            validation.get("expected_real_switch_dpid"),
            "validation.expected_real_switch_dpid",
        )
    config["validation"] = validation
    return config


def format_external_link_ports(config):
    pairs = config["hybrid"]["external_link_ports"]
    return ",".join(f"{int(item['dpid'])}:{int(item['port'])}" for item in pairs)


def format_real_routes(config):
    return ",".join(config["hybrid"]["real_routes"])


def build_runtime_env(config):
    return {
        "EXTERNAL_INTF": config["external_interface"],
        "EXTERNAL_LINK_PORTS": format_external_link_ports(config),
        "CONTROLLER_PORTS": " ".join(str(port) for port in config["controllers"]["ports"]),
        "VALIDATION_VIRTUAL_HOST_NAME": config["validation"]["virtual_host_name"],
        "HYBRID_GATEWAY_IP": config["hybrid"]["gateway_ip"],
        "HYBRID_GATEWAY_MAC": config["hybrid"]["gateway_mac"],
        "HYBRID_REAL_ROUTES": format_real_routes(config),
    }


def print_shell_env(config):
    for key, value in build_runtime_env(config).items():
        print(f"export {key}={shlex.quote(str(value))}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Load hybrid acceptance config.")
    parser.add_argument("--config", default="config/hybrid_acceptance.json")
    parser.add_argument("--shell-env", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        config = load_acceptance_config(args.config)
    except AcceptanceConfigError as exc:
        print(f"acceptance config error: {exc}", file=sys.stderr)
        return 1

    if args.shell_env:
        print_shell_env(config)
    elif args.json:
        print(json.dumps(config, ensure_ascii=False, indent=2))
    else:
        print(f"acceptance config ok: {args.config}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
