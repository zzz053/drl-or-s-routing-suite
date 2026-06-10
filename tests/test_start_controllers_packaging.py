from pathlib import Path

from start_controllers_test import select_controller_app


def test_select_controller_app_prefers_source_when_available(tmp_path):
    (tmp_path / "controller.py").write_text("# source\n", encoding="utf-8")
    (tmp_path / "controller.pyc").write_bytes(b"bytecode")

    assert select_controller_app(tmp_path) == "controller.py"


def test_select_controller_app_uses_bytecode_in_delivery_runtime(tmp_path):
    (tmp_path / "controller.pyc").write_bytes(b"bytecode")

    assert select_controller_app(tmp_path) == "controller.pyc"
