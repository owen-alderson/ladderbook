from ladderbook.record.writer import RawWriter, read_raw


def test_rotates_after_interval_and_reads_back_in_order(tmp_path):
    writer = RawWriter(tmp_path, rotate_seconds=10)
    base = 1_791_500_000_000_000_000
    writer.write("kalshi", '{"a":1}', recv_ns=base)
    writer.write("kalshi", '{"a":2}', recv_ns=base + 5 * 10**9)
    assert writer.files_written == 0
    writer.write("deribit", '{"a":3}', recv_ns=base + 11 * 10**9)  # crosses the interval
    assert writer.files_written == 1
    writer.write("kalshi", '{"a":4}', recv_ns=base + 12 * 10**9)
    writer.flush()
    table = read_raw(tmp_path)
    assert table.column("raw").to_pylist() == ['{"a":1}', '{"a":2}', '{"a":3}', '{"a":4}']
    assert table.column("source").to_pylist()[2] == "deribit"
    assert not list(tmp_path.rglob("*.tmp"))


def test_flush_with_nothing_buffered_writes_nothing(tmp_path):
    assert RawWriter(tmp_path).flush() is None
    assert read_raw(tmp_path).num_rows == 0
