from external_host_guard import (
    is_external_host_source,
    purge_host_records_for_source,
    remember_external_host_source,
)


def test_remembered_external_host_source_matches_mac_and_ip():
    sources = set()

    remember_external_host_source(sources, "AA:BB:CC:00:00:01", "10.5.0.13")

    assert is_external_host_source(sources, "aa:bb:cc:00:00:01", "10.5.0.13")
    assert not is_external_host_source(sources, "aa:bb:cc:00:00:01", "10.5.0.14")


def test_purge_host_records_for_external_source_removes_only_that_host():
    host_to_sw_port = {
        28: {
            5: [["f4:84:8d:99:42:72", "10.5.0.13"], ["aa:aa:aa:aa:aa:aa", "10.0.0.28"]],
            6: [["f4:84:8d:99:42:72", "10.5.0.13"]],
        },
        31: {
            2: [["bb:bb:bb:bb:bb:bb", "10.0.0.31"]],
        },
    }

    removed = purge_host_records_for_source(host_to_sw_port, "F4:84:8D:99:42:72", "10.5.0.13")

    assert removed == [
        (28, 5, ["f4:84:8d:99:42:72", "10.5.0.13"]),
        (28, 6, ["f4:84:8d:99:42:72", "10.5.0.13"]),
    ]
    assert host_to_sw_port == {
        28: {
            5: [["aa:aa:aa:aa:aa:aa", "10.0.0.28"]],
        },
        31: {
            2: [["bb:bb:bb:bb:bb:bb", "10.0.0.31"]],
        },
    }
