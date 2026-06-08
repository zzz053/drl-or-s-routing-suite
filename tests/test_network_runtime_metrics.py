import math
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_port_desc_speed_prefers_current_then_max_then_configured_then_default():
    from network_metrics import DEFAULT_LINK_CAPACITY_MBPS, choose_capacity_mbps

    capacity, source = choose_capacity_mbps(curr_speed_kbps=1_000_000, max_speed_kbps=10_000_000)
    assert capacity == 1000.0
    assert source == "openflow_curr_speed"

    capacity, source = choose_capacity_mbps(curr_speed_kbps=0, max_speed_kbps=10_000_000)
    assert capacity == 10000.0
    assert source == "openflow_max_speed"

    capacity, source = choose_capacity_mbps(curr_speed_kbps=0, max_speed_kbps=0, configured_mbps=800)
    assert capacity == 800.0
    assert source == "configured"

    capacity, source = choose_capacity_mbps(curr_speed_kbps=0, max_speed_kbps=0)
    assert capacity == DEFAULT_LINK_CAPACITY_MBPS
    assert source == "default"


def test_port_stats_delta_calculates_real_throughput_utilization_and_health():
    from network_metrics import calculate_port_runtime_metrics

    previous = {
        "tx_bytes": 1_000_000,
        "rx_bytes": 2_000_000,
        "tx_packets": 1000,
        "rx_packets": 2000,
        "tx_dropped": 0,
        "rx_dropped": 0,
        "tx_errors": 0,
        "rx_errors": 0,
        "timestamp": 10.0,
    }
    current = {
        "tx_bytes": 2_250_000,
        "rx_bytes": 2_625_000,
        "tx_packets": 2000,
        "rx_packets": 3000,
        "tx_dropped": 10,
        "rx_dropped": 5,
        "tx_errors": 2,
        "rx_errors": 1,
        "timestamp": 20.0,
    }

    metrics = calculate_port_runtime_metrics(previous, current, capacity_mbps=1000.0)

    assert metrics["tx_mbps"] == 1.0
    assert metrics["rx_mbps"] == 0.5
    assert metrics["throughput_mbps"] == 1.5
    assert metrics["free_bandwidth_mbps"] == 999.0
    assert metrics["utilization_percent"] == 0.1
    assert 0 < metrics["drop_rate"] < 0.01
    assert 0 < metrics["error_rate"] < 0.01


def test_aggregate_network_metrics_uses_real_edge_values():
    from network_metrics import aggregate_network_metrics

    edges = [
        {"throughput_mbps": 10, "utilization_percent": 20, "error_rate": 0.0, "port_state": "LIVE"},
        {"throughput_mbps": 30, "utilization_percent": 80, "error_rate": 0.2, "port_state": "LIVE"},
        {"edge_type": "host_switch", "throughput_mbps": 999},
    ]

    result = aggregate_network_metrics(edges)

    assert result["throughput_mbps"] == 40.0
    assert result["avg_utilization_percent"] == 50.0
    assert result["max_utilization_percent"] == 80.0
    assert result["active_link_count"] == 2
    assert result["error_link_count"] == 1


def test_controller_requests_port_desc_and_reports_runtime_metric_fields():
    text = (ROOT / "controller.py").read_text(encoding="utf-8")

    assert "OFPPortDescStatsRequest" in text
    assert "EventOFPPortDescStatsReply" in text
    assert "capacity_mbps" in text
    assert "throughput_mbps" in text
    assert "utilization_percent" in text
    assert "bandwidth_source" in text


def test_server_agent_keeps_openflow_metrics_when_configured_static_link_arrives_later():
    text = (ROOT / "server_agent.py").read_text(encoding="utf-8")

    assert "existing_source.startswith('openflow_')" in text
    assert "not incoming_source.startswith('openflow_')" in text
    assert "bw = existing_edge.get('bw', bw)" in text
