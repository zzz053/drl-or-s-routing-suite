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
    "static_links": [],
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
DEFAULT_SERVICES = {
    "server_agent": {
        "bind_ip": "0.0.0.0",
        "connect_ip": "127.0.0.1",
        "port": 6001,
        "log_level": "INFO",
    },
    "path_service": {
        "host": "127.0.0.1",
        "port": 8889,
        "topo": "Military",
        "model_dir": "model/Military_mininet",
    },
    "web": {
        "port": 6009,
    },
}
DEFAULT_STARTUP = {
    "log_dir": "logs",
    "report_dir": "reports",
    "path_service_ready_timeout_seconds": 90,
    "controller_ready_timeout_seconds": 90,
    "mininet_routes_ready_timeout_seconds": 240,
}
DEFAULT_SAFETY = {
    "allow_external_interface_default_route": False,
}
DEFAULT_LOAD_TEST = {
    "flows": 20,
    "duration": 10,
    "parallel": 5,
    "seed": "",
    "udp": False,
    "bandwidth": "10M",
}
DEFAULT_WEB = {
    "mode": "read_only",
}
DEFAULT_TRAFFIC_CLASSES = [
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


def _require_port(value, name):
    port = _require_int(value, name)
    if port <= 0 or port > 65535:
        raise AcceptanceConfigError(f"{name} must be within 1..65535")
    return port


def _require_non_negative_int(value, name):
    number = _require_int(value, name)
    if number < 0:
        raise AcceptanceConfigError(f"{name} must be non-negative")
    return number


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


def _normalize_static_links(value):
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise AcceptanceConfigError("hybrid.static_links must be a list")
    links = []
    for idx, item in enumerate(value):
        item = _require_mapping(item, f"hybrid.static_links[{idx}]")
        link = {
            "src_dpid": _require_int(item.get("src_dpid"), f"hybrid.static_links[{idx}].src_dpid"),
            "src_port": _require_int(item.get("src_port"), f"hybrid.static_links[{idx}].src_port"),
            "dst_dpid": _require_int(item.get("dst_dpid"), f"hybrid.static_links[{idx}].dst_dpid"),
            "dst_port": _require_int(item.get("dst_port"), f"hybrid.static_links[{idx}].dst_port"),
            "delay_ms": _require_float(item.get("delay_ms", 0.0), f"hybrid.static_links[{idx}].delay_ms"),
            "bandwidth_mbps": _require_float(
                item.get("bandwidth_mbps", 1.0),
                f"hybrid.static_links[{idx}].bandwidth_mbps",
            ),
            "loss_percent": _require_float(
                item.get("loss_percent", 0.0),
                f"hybrid.static_links[{idx}].loss_percent",
            ),
            "source": _require_string(
                item.get("source", "configured_static_link"),
                f"hybrid.static_links[{idx}].source",
            ),
        }
        if link["src_dpid"] == link["dst_dpid"]:
            raise AcceptanceConfigError(f"hybrid.static_links[{idx}] must connect two different dpids")
        if link["src_port"] <= 0 or link["dst_port"] <= 0:
            raise AcceptanceConfigError(f"hybrid.static_links[{idx}] ports must be positive")
        if link["delay_ms"] < 0:
            raise AcceptanceConfigError(f"hybrid.static_links[{idx}].delay_ms must be non-negative")
        if link["bandwidth_mbps"] <= 0:
            raise AcceptanceConfigError(f"hybrid.static_links[{idx}].bandwidth_mbps must be positive")
        if link["loss_percent"] < 0:
            raise AcceptanceConfigError(f"hybrid.static_links[{idx}].loss_percent must be non-negative")
        links.append(link)
    return links


def _normalize_traffic_classes(value):
    if value in (None, ""):
        value = DEFAULT_TRAFFIC_CLASSES
    if not isinstance(value, list) or not value:
        raise AcceptanceConfigError("traffic_classes must be a non-empty list")
    classes = []
    seen_names = set()
    for idx, item in enumerate(value):
        item = _require_mapping(item, f"traffic_classes[{idx}]")
        cls = {
            "name": _require_string(item.get("name"), f"traffic_classes[{idx}].name"),
            "port_start": _require_int(item.get("port_start"), f"traffic_classes[{idx}].port_start"),
            "port_end": _require_int(item.get("port_end"), f"traffic_classes[{idx}].port_end"),
            "drl_type": _require_int(item.get("drl_type"), f"traffic_classes[{idx}].drl_type"),
            "route_policy": _require_string(item.get("route_policy"), f"traffic_classes[{idx}].route_policy"),
            "flow_priority": _require_int(item.get("flow_priority"), f"traffic_classes[{idx}].flow_priority"),
            "drl_demand_kbps": _require_int(
                item.get("drl_demand_kbps"),
                f"traffic_classes[{idx}].drl_demand_kbps",
            ),
            "drl_duration": _require_int(item.get("drl_duration"), f"traffic_classes[{idx}].drl_duration"),
        }
        if cls["name"] in seen_names:
            raise AcceptanceConfigError(f"traffic_classes[{idx}].name must be unique")
        seen_names.add(cls["name"])
        if cls["port_start"] <= 0 or cls["port_end"] < cls["port_start"] or cls["port_end"] > 65535:
            raise AcceptanceConfigError(f"traffic_classes[{idx}] ports must be within 1..65535")
        if cls["drl_type"] not in {0, 1, 2}:
            raise AcceptanceConfigError(f"traffic_classes[{idx}].drl_type must be one of 0, 1, 2")
        if cls["flow_priority"] <= 0:
            raise AcceptanceConfigError(f"traffic_classes[{idx}].flow_priority must be positive")
        if cls["drl_demand_kbps"] <= 0:
            raise AcceptanceConfigError(f"traffic_classes[{idx}].drl_demand_kbps must be positive")
        if cls["drl_duration"] <= 0:
            raise AcceptanceConfigError(f"traffic_classes[{idx}].drl_duration must be positive")
        classes.append(cls)
    return classes


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

    raw_services = _require_mapping(raw.get("services", {}), "services")
    services = {
        "server_agent": dict(DEFAULT_SERVICES["server_agent"]),
        "path_service": dict(DEFAULT_SERVICES["path_service"]),
        "web": dict(DEFAULT_SERVICES["web"]),
    }
    for section in services:
        services[section].update(_require_mapping(raw_services.get(section, {}), f"services.{section}"))
    services["server_agent"]["bind_ip"] = _require_string(
        services["server_agent"].get("bind_ip"),
        "services.server_agent.bind_ip",
    )
    services["server_agent"]["connect_ip"] = _require_string(
        services["server_agent"].get("connect_ip"),
        "services.server_agent.connect_ip",
    )
    services["server_agent"]["port"] = _require_port(
        services["server_agent"].get("port"),
        "services.server_agent.port",
    )
    services["server_agent"]["log_level"] = _require_string(
        services["server_agent"].get("log_level"),
        "services.server_agent.log_level",
    ).upper()
    if services["server_agent"]["log_level"] not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise AcceptanceConfigError(
            "services.server_agent.log_level must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL"
        )
    services["path_service"]["host"] = _require_string(
        services["path_service"].get("host"),
        "services.path_service.host",
    )
    services["path_service"]["port"] = _require_port(
        services["path_service"].get("port"),
        "services.path_service.port",
    )
    services["path_service"]["topo"] = _require_string(
        services["path_service"].get("topo"),
        "services.path_service.topo",
    )
    services["path_service"]["model_dir"] = _require_string(
        services["path_service"].get("model_dir"),
        "services.path_service.model_dir",
    )
    services["web"]["port"] = _require_port(
        services["web"].get("port"),
        "services.web.port",
    )
    config["services"] = services

    startup = dict(DEFAULT_STARTUP)
    startup.update(_require_mapping(raw.get("startup", {}), "startup"))
    startup["log_dir"] = _require_string(startup.get("log_dir"), "startup.log_dir")
    startup["report_dir"] = _require_string(startup.get("report_dir"), "startup.report_dir")
    startup["path_service_ready_timeout_seconds"] = _require_non_negative_int(
        startup.get("path_service_ready_timeout_seconds"),
        "startup.path_service_ready_timeout_seconds",
    )
    startup["controller_ready_timeout_seconds"] = _require_non_negative_int(
        startup.get("controller_ready_timeout_seconds"),
        "startup.controller_ready_timeout_seconds",
    )
    startup["mininet_routes_ready_timeout_seconds"] = _require_non_negative_int(
        startup.get("mininet_routes_ready_timeout_seconds"),
        "startup.mininet_routes_ready_timeout_seconds",
    )
    config["startup"] = startup

    safety = dict(DEFAULT_SAFETY)
    safety.update(_require_mapping(raw.get("safety", {}), "safety"))
    safety["allow_external_interface_default_route"] = _require_bool(
        safety.get("allow_external_interface_default_route"),
        "safety.allow_external_interface_default_route",
    )
    config["safety"] = safety

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
    hybrid["static_links"] = _normalize_static_links(
        hybrid.get("static_links")
    )
    config["hybrid"] = hybrid

    config["traffic_classes"] = _normalize_traffic_classes(raw.get("traffic_classes"))

    web = dict(DEFAULT_WEB)
    web.update(_require_mapping(raw.get("web", {}), "web"))
    web["mode"] = _require_string(web.get("mode"), "web.mode").lower()
    if web["mode"] not in {"read_only", "development"}:
        raise AcceptanceConfigError("web.mode must be one of read_only, development")
    config["web"] = web

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


def format_static_hybrid_links(config):
    return json.dumps(
        config["hybrid"].get("static_links", []),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def format_traffic_classes(config):
    return json.dumps(
        config.get("traffic_classes", DEFAULT_TRAFFIC_CLASSES),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def build_runtime_env(config):
    runtime = config["runtime"]
    hybrid = config["hybrid"]
    load_test = config["load_test"]
    services = config.get("services", {})
    server_agent = dict(DEFAULT_SERVICES["server_agent"])
    server_agent.update(services.get("server_agent", {}))
    path_service = dict(DEFAULT_SERVICES["path_service"])
    path_service.update(services.get("path_service", {}))
    web = dict(DEFAULT_SERVICES["web"])
    web.update(services.get("web", {}))
    startup = dict(DEFAULT_STARTUP)
    startup.update(config.get("startup", {}))
    safety = dict(DEFAULT_SAFETY)
    safety.update(config.get("safety", {}))
    return {
        "EXTERNAL_INTF": config["external_interface"],
        "EXTERNAL_SWITCH": hybrid["external_switch"],
        "EXTERNAL_PORT": str(hybrid["external_port"]),
        "EXTERNAL_LINK_PORTS": format_external_link_ports(config),
        "CONTROLLER_PORTS": " ".join(str(port) for port in config["controllers"]["ports"]),
        "SERVER_AGENT_BIND_IP": server_agent["bind_ip"],
        "SERVER_AGENT_IP": server_agent["connect_ip"],
        "SERVER_AGENT_PORT": str(server_agent["port"]),
        "SERVER_AGENT_LOG_LEVEL": server_agent["log_level"],
        "PATH_SERVICE_HOST": path_service["host"],
        "PATH_SERVICE_PORT": str(path_service["port"]),
        "PATH_SERVICE_TOPO": path_service["topo"],
        "PATH_SERVICE_MODEL_DIR": path_service["model_dir"],
        "WEB_PORT": str(web["port"]),
        "SERVER_AGENT_ROUTE_MODE": runtime["route_mode"],
        "DRL_ROUTE_MODE": runtime["route_mode"],
        "DRL_K_CANDIDATES": str(runtime["drl_k_candidates"]),
        "DRL_INFERENCE_TIMEOUT_MS": str(runtime["drl_inference_timeout_ms"]),
        "DRL_MIN_CONFIDENCE": str(runtime["drl_min_confidence"]),
        "ROUTE_FLOW_IDLE_TIMEOUT": str(runtime["route_flow_idle_timeout"]),
        "ROUTE_FLOW_HARD_TIMEOUT": str(runtime["route_flow_hard_timeout"]),
        "FLOW_INSTALL_BARRIER_TIMEOUT": str(runtime["flow_install_barrier_timeout"]),
        "WEB_MODE": config.get("web", DEFAULT_WEB)["mode"],
        "EXTERNAL_ARP_ALLOWED_PREFIXES": ",".join(hybrid["external_arp_allowed_prefixes"]),
        "VIRTUAL_SWITCH_DPID_MAX": str(hybrid["virtual_switch_dpid_max"]),
        "EXTERNAL_LINK_METRICS_JSON": format_external_link_metrics(config),
        "STATIC_HYBRID_LINKS_JSON": format_static_hybrid_links(config),
        "TRAFFIC_CLASSES_JSON": format_traffic_classes(config),
        "LOAD_TEST_FLOWS": str(load_test["flows"]),
        "LOAD_TEST_DURATION": str(load_test["duration"]),
        "LOAD_TEST_PARALLEL": str(load_test["parallel"]),
        "LOAD_TEST_SEED": str(load_test["seed"]),
        "LOAD_TEST_UDP": "1" if load_test["udp"] else "0",
        "LOAD_TEST_BANDWIDTH": load_test["bandwidth"],
        "VALIDATION_VIRTUAL_HOST_NAME": config["validation"]["virtual_host_name"],
        "LOG_DIR": startup["log_dir"],
        "REPORT_DIR": startup["report_dir"],
        "PATH_SERVICE_READY_TIMEOUT_SECONDS": str(startup["path_service_ready_timeout_seconds"]),
        "CONTROLLER_READY_TIMEOUT_SECONDS": str(startup["controller_ready_timeout_seconds"]),
        "MININET_ROUTES_READY_TIMEOUT_SECONDS": str(startup["mininet_routes_ready_timeout_seconds"]),
        "ALLOW_EXTERNAL_INTF_HAS_DEFAULT_ROUTE": (
            "1" if safety["allow_external_interface_default_route"] else "0"
        ),
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
