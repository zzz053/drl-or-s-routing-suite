import importlib


def test_start_controllers_test_reads_controller_ports_from_environment(monkeypatch):
    monkeypatch.setenv("CONTROLLER_PORTS", "6654 7777")

    import start_controllers_test
    importlib.reload(start_controllers_test)

    assert start_controllers_test.TEST_CONTROLLER_PORTS == [6654, 7777]
