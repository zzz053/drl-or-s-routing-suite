from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_web_homepage_contains_acceptance_status_card():
    text = (ROOT / "web_ui_html.py").read_text(encoding="utf-8")

    assert "acceptance-status-card" in text
    assert "虚实通信状态" in text
    assert "控制面/最近路径状态" in text
    assert "/api/acceptance/status" in text
    assert "实时 ping" not in text
