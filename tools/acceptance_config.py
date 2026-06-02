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
    "external_arp_allowed_prefixes": ["10.0.0.0/24"],
    "virtual_switch_dpid_max": 1000,
    "external_link_metrics": [],
}
DEFAULT_RUNTIME = {
    "route_mode": "hybrid",
    "drl_k_candidates": 5,
    "drl_inference_timeout_ms": 100,
    "drl_min_confidence": 0.50,
    "route_flow_idle_timeout": 120,
    "route_flow_hard_timeout": 0,
    "flow_install_barrier_timeout": 0.5,
}
DEFAULT_LOAD_TEST = {
    "flows": 20,
    "duration": 10,
    "parallel": 5,
    "seed": "",
    "udp": False,
    "bandwidth": "10M",
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


def _require_float(value, name):
    try:
        return float(value)
    except (TypeError, ValueError):
        raise AcceptanceConfigError(f"{name} must be a number")


def _require_bool(value, name):
    if isinstance(value, bool):
        return value
    raise AcceptanceConfigError(f"{name} must be a boolean")


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


def _normalize_external_link_metrics(value):
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise AcceptanceConfigError("hybrid.external_link_metrics must be a list")
    metrics = []
    for idx, item in enumerate(value):
        item = _require_mapping(item, f"hybrid.external_link_metrics[{idx}]")
        metric = {
            "dpid": _require_int(item.get("dpid"), f"hybrid.external_link_metrics[{idx}].dpid"),
            "port": _require_int(item.get("port"), f"hybrid.external_link_metrics[{idx}].port"),
            "delay_ms": _require_float(item.get("delay_ms", 0.0), f"hybrid.external_link_metrics[{idx}].delay_ms"),
            "bandwidth_mbps": _require_float(
                item.get("bandwidth_mbps", 1.0),
                f"hybrid.external_link_metrics[{idx}].bandwidth_mbps",
            ),
            "loss_percent": _require_float(
                item.get("loss_percent", 0.0),
                f"hybrid.external_link_metrics[{idx}].loss_percent",
            ),
            "source": _require_string(
                item.get("source", "configured"),
                f"hybrid.external_link_metrics[{idx}].source",
            ),
        }
        if metric["delay_ms"] < 0:
            raise AcceptanceConfigError(f"hybrid.external_link_metrics[{idx}].delay_ms must be non-negative")
        if metric["bandwidth_mbps"] <= 0:
            raise AcceptanceConfigError(f"hybrid.external_link_metrics[{idx}].bandwidth_mbps must be positive")
        if metric["loss_percent"] < 0:
            raise AcceptanceConfigError(f"hybrid.external_link_metrics[{idx}].loss_percent must be non-negative")
        metrics.append(metric)
    return metrics


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

    runtime = dict(DEFAULT_RUNTIME)
    runtime.update(_require_mapping(raw.get("runtime", {}), "runtime"))
    runtime["route_mode"] = _require_string(runtime.get("route_mode"), "runtime.route_mode").lower()
    if runtime["route_mode"] not in {"spf", "shadow", "hybrid", "drl"}:
        raise AcceptanceConfigError("runtime.route_mode must be one of spf, shadow, hybrid, drl")
    runtime["drl_k_candidates"] = _require_int(runtime.get("drl_k_candidates"), "runtime.drl_k_candidates")
    runtime["drl_inference_timeout_ms"] = _require_int(
        runtime.get("drl_inference_timeout_ms"),
        "runtime.drl_inference_timeout_ms",
    )
    runtime["drl_min_confidence"] = _require_float(
        runtime.get("drl_min_confidence"),
        "runtime.drl_min_confidence",
    )
    runtime["route_flow_idle_timeout"] = _require_int(
        runtime.get("route_flow_idle_timeout"),
        "runtime.route_flow_idle_timeout",
    )
    runtime["route_flow_hard_timeout"] = _require_int(
        runtime.get("route_flow_hard_timeout"),
        "runtime.route_flow_hard_timeout",
    )
    runtime["flow_install_barrier_timeout"] = _require_float(
        runtime.get("flow_install_barrier_timeout"),
        "runtime.flow_install_barrier_timeout",
    )
    config["runtime"] = runtime

    hybrid = dict(DEFAULT_HYBRID)
    hybrid.update(_require_mapping(raw.get("hybrid"), "hybrid"))
    hybrid["external_link_ports"] = _normalize_external_link_ports(
        hybrid.get("external_link_ports")
    )
    first_link = hybrid["external_link_ports"][0]
    default_switch = f"s{first_link['dpid']}"
    hybrid["external_switch"] = _require_string(
        hybrid.get("external_switch", default_switch),
        "hybrid.external_switch",
    )
    hybrid["external_port"] = _require_int(
        hybrid.get("external_port", first_link["port"]),
        "hybrid.external_port",
    )
    if hybrid["external_switch"] != default_switch:
        raise AcceptanceConfigError("hybrid.external_switch must match hybrid.external_link_ports[0].dpid")
    if hybrid["external_port"] != first_link["port"]:
        raise AcceptanceConfigError("hybrid.external_port must match hybrid.external_link_ports[0].port")
    hybrid["gateway_ip"] = _require_string(hybrid.get("gateway_ip"), "hybrid.gateway_ip")
    hybrid["gateway_mac"] = _require_string(hybrid.get("gateway_mac"), "hybrid.gateway_mac")
    hybrid["real_routes"] = _require_string_list(hybrid.get("real_routes"), "hybrid.real_routes")
    hybrid["external_arp_allowed_prefixes"] = _require_string_list(
        hybrid.get("external_arp_allowed_prefixes"),
        "hybrid.external_arp_allowed_prefixes",
    )
    hybrid["virtual_switch_dpid_max"] = _require_int(
        hybrid.get("virtual_switch_dpid_max"),
        "hybrid.virtual_switch_dpid_max",
    )
    hybrid["external_link_metrics"] = _normalize_external_link_metrics(
        hybrid.get("external_link_metrics")
    )
    config["hybrid"] = hybrid

    load_test = dict(DEFAULT_LOAD_TEST)
    load_test.update(_require_mapping(raw.get("load_test", {}), "load_test"))
    load_test["flows"] = _require_int(load_test.get("flows"), "load_test.flows")
    load_test["duration"] = _require_int(load_test.get("duration"), "load_test.duration")
    load_test["parallel"] = _require_int(load_test.get("parallel"), "load_test.parallel")
    seed = load_test.get("seed", "")
    load_test["seed"] = "" if seed in (None, "") else _require_int(seed, "load_test.seed")
    load_test["udp"] = _require_bool(load_test.get("udp"), "load_test.udp")
    load_test["bandwidth"] = _require_string(load_test.get("bandwidth"), "load_test.bandwidth")
    config["load_test"] = load_test

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


def format_external_link_metrics(config):
    return json.dumps(
        config["hybrid"].get("external_link_metrics", []),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def build_runtime_env(config):
    runtime = config["runtime"]
    hybrid = config["hybrid"]
    load_test = config["load_test"]
    return {
        "EXTERNAL_INTF": config["external_interface"],
        "EXTERNAL_SWITCH": hybrid["external_switch"],
        "EXTERNAL_PORT": str(hybrid["external_port"]),
        "EXTERNAL_LINK_PORTS": format_external_link_ports(config),
        "CONTROLLER_PORTS": " ".join(str(port) for port in config["controllers"]["ports"]),
        "SERVER_AGENT_ROUTE_MODE": runtime["route_mode"],
        "DRL_ROUTE_MODE": runtime["route_mode"],
        "DRL_K_CANDIDATES": str(runtime["drl_k_candidates"]),
        "DRL_INFERENCE_TIMEOUT_MS": str(runtime["drl_inference_timeout_ms"]),
        "DRL_MIN_CONFIDENCE": str(runtime["drl_min_confidence"]),
        "ROUTE_FLOW_IDLE_TIMEOUT": str(runtime["route_flow_idle_timeout"]),
        "ROUTE_FLOW_HARD_TIMEOUT": str(runtime["route_flow_hard_timeout"]),
        "FLOW_INSTALL_BARRIER_TIMEOUT": str(runtime["flow_install_barrier_timeout"]),
        "EXTERNAL_ARP_ALLOWED_PREFIXES": ",".join(hybrid["external_arp_allowed_prefixes"]),
        "VIRTUAL_SWITCH_DPID_MAX": str(hybrid["virtual_switch_dpid_max"]),
        "EXTERNAL_LINK_METRICS_JSON": format_external_link_metrics(config),
        "LOAD_TEST_FLOWS": str(load_test["flows"]),
        "LOAD_TEST_DURATION": str(load_test["duration"]),
        "LOAD_TEST_PARALLEL": str(load_test["parallel"]),
        "LOAD_TEST_SEED": str(load_test["seed"]),
        "LOAD_TEST_UDP": "1" if load_test["udp"] else "0",
        "LOAD_TEST_BANDWIDTH": load_test["bandwidth"],
        "VALIDATION_VIRTUAL_HOST_NAME": config["validation"]["virtual_host_name"],
        "HYBRID_GATEWAY_IP": hybrid["gateway_ip"],
        "HYBRID_GATEWAY_MAC": hybrid["gateway_mac"],
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
