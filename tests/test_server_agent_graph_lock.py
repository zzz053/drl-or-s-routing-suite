from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER_AGENT = ROOT / "server_agent.py"


def test_update_graph_uses_dedicated_reentrant_lock():
    source = SERVER_AGENT.read_text(encoding="utf-8")

    assert "self.graph_lock = threading.RLock()" in source
    assert "graph_lock = getattr(self, 'graph_lock', None)" in source
    assert "with graph_lock:" in source
    assert "return self._update_graph_locked()" in source
    assert "def _update_graph_locked(self):" in source
    assert "_graph_update_locked" not in source


def test_update_graph_upserts_switch_nodes_without_check_then_get():
    source = SERVER_AGENT.read_text(encoding="utf-8")

    assert "def _upsert_graph_switch_node" in source
    assert "self._upsert_graph_switch_node(src)" in source
    assert "self._upsert_graph_switch_node(dst)" in source
