import importlib
import json


def test_external_link_ports_parses_dpid_port_pairs(monkeypatch):
    monkeypatch.setenv("EXTERNAL_LINK_PORTS", "1:20, 42:7")

    import common_config
    importlib.reload(common_config)

    assert common_config.EXTERNAL_LINK_PORTS == {1: {20}, 42: {7}}


def test_controller_applies_external_link_port_whitelist():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "controller.py").read_text(encoding="utf-8")

    assert "EXTERNAL_LINK_PORTS" in text
    assert "_apply_configured_external_link_ports" in text


def test_external_link_metrics_parses_json_metrics(monkeypatch):
    monkeypatch.setenv(
        "EXTERNAL_LINK_METRICS_JSON",
        json.dumps([
            {
                "dpid": 1,
                "port": 20,
                "delay_ms": 2.5,
                "bandwidth_mbps": 800,
                "loss_percent": 0.1,
                "source": "configured",
            }
        ]),
    )

    import common_config
    importlib.reload(common_config)

    assert common_config.EXTERNAL_LINK_METRICS[(1, 20)]["delay_seconds"] == 0.0025
    assert common_config.EXTERNAL_LINK_METRICS[(1, 20)]["bandwidth_mbps"] == 800.0
    assert common_config.EXTERNAL_LINK_METRICS[(1, 20)]["loss_percent"] == 0.1


def test_controller_uses_configured_external_link_metrics():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "controller.py").read_text(encoding="utf-8")

    assert "EXTERNAL_LINK_METRICS" in text
    assert "_configured_external_link_metric" in text
