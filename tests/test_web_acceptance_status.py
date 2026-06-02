from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_web_homepage_does_not_render_acceptance_status_card():
    text = (ROOT / "web_ui_html.py").read_text(encoding="utf-8")

    assert "acceptance-status-card" not in text
    assert "updateAcceptanceStatus" not in text
    assert "/api/acceptance/status" not in text
