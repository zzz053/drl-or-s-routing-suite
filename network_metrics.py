"""Pure helpers for OpenFlow port and network runtime metrics."""

import math


DEFAULT_LINK_CAPACITY_MBPS = 800.0


def _finite_non_negative(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(number) or number < 0:
        return float(default)
    return number


def choose_capacity_mbps(
    curr_speed_kbps=0,
    max_speed_kbps=0,
    configured_mbps=None,
    default_mbps=DEFAULT_LINK_CAPACITY_MBPS,
):
    """Choose port capacity using OpenFlow speed, configured fallback, then default."""
    current = _finite_non_negative(curr_speed_kbps)
    if current > 0:
        return current / 1000.0, "openflow_curr_speed"

    maximum = _finite_non_negative(max_speed_kbps)
    if maximum > 0:
        return maximum / 1000.0, "openflow_max_speed"

    configured = _finite_non_negative(configured_mbps)
    if configured > 0:
        return configured, "configured"

    fallback = _finite_non_negative(default_mbps, DEFAULT_LINK_CAPACITY_MBPS)
    if fallback <= 0:
        fallback = DEFAULT_LINK_CAPACITY_MBPS
    return fallback, "default"


def calculate_port_runtime_metrics(previous, current, capacity_mbps):
    """Calculate rates from adjacent monotonically increasing OpenFlow counters."""
    period = _finite_non_negative(current.get("timestamp")) - _finite_non_negative(previous.get("timestamp"))
    capacity = max(_finite_non_negative(capacity_mbps, DEFAULT_LINK_CAPACITY_MBPS), 0.000001)

    def delta(name):
        return max(_finite_non_negative(current.get(name)) - _finite_non_negative(previous.get(name)), 0.0)

    if period <= 0:
        period = 1.0

    tx_mbps = delta("tx_bytes") * 8.0 / period / 1_000_000.0
    rx_mbps = delta("rx_bytes") * 8.0 / period / 1_000_000.0
    tx_packets = delta("tx_packets")
    rx_packets = delta("rx_packets")
    tx_dropped = delta("tx_dropped")
    rx_dropped = delta("rx_dropped")
    tx_errors = delta("tx_errors")
    rx_errors = delta("rx_errors")
    total_packets = tx_packets + rx_packets + tx_dropped + rx_dropped

    return {
        "tx_mbps": tx_mbps,
        "rx_mbps": rx_mbps,
        "throughput_mbps": tx_mbps + rx_mbps,
        "free_bandwidth_mbps": max(capacity - tx_mbps, 0.0),
        "utilization_percent": min(tx_mbps / capacity * 100.0, 100.0),
        "drop_rate": (tx_dropped + rx_dropped) / total_packets if total_packets > 0 else 0.0,
        "error_rate": (tx_errors + rx_errors) / total_packets if total_packets > 0 else 0.0,
        "rx_errors": int(_finite_non_negative(current.get("rx_errors"))),
        "tx_errors": int(_finite_non_negative(current.get("tx_errors"))),
        "rx_dropped": int(_finite_non_negative(current.get("rx_dropped"))),
        "tx_dropped": int(_finite_non_negative(current.get("tx_dropped"))),
    }


def aggregate_network_metrics(edges):
    """Aggregate real runtime values from directed switch-link edges."""
    links = [edge for edge in edges if (edge or {}).get("edge_type", "switch_link") == "switch_link"]
    throughputs = [_finite_non_negative(edge.get("throughput_mbps")) for edge in links]
    utilizations = [_finite_non_negative(edge.get("utilization_percent")) for edge in links]
    active = [
        edge for edge in links
        if str(edge.get("port_state", "LIVE")).upper() not in {"DOWN", "LINK_DOWN", "BLOCKED"}
    ]
    error_links = [
        edge for edge in links
        if _finite_non_negative(edge.get("error_rate")) > 0
        or _finite_non_negative(edge.get("drop_rate")) > 0
    ]
    return {
        "throughput_mbps": sum(throughputs),
        "avg_utilization_percent": sum(utilizations) / len(utilizations) if utilizations else 0.0,
        "max_utilization_percent": max(utilizations, default=0.0),
        "active_link_count": len(active),
        "error_link_count": len(error_links),
    }
