from kalshi_msgs import delta, note, snapshot, subscribe_command, subscribed

from ladderbook.record.verify import verify_rows


def _tape(*kalshi, use_yes_price=True):
    rows = [("ladderbook", note("connected")), ("ladderbook", subscribe_command(use_yes_price))]
    rows += [("kalshi", subscribed("orderbook_delta", 1))]
    return rows + [("kalshi", m) for m in kalshi]


def test_rebuilt_book_matching_the_next_snapshot_passes():
    report = verify_rows(
        _tape(
            snapshot("X", 1, yes=[("0.4000", "10.00")], no=[("0.6000", "5.00")]),
            delta("X", 2, "0.4000", "-4.00", "yes"),
            delta("X", 3, "0.4100", "2.50", "yes"),
            delta("X", 4, "0.6000", "-5.00", "no"),
            delta("X", 5, "0.6200", "1.00", "no"),
            snapshot("X", 6, yes=[("0.4000", "6.00"), ("0.4100", "2.50")], no=[("0.6200", "1.00")]),
        )
    )
    assert report.ok
    assert (report.snapshots_compared, report.snapshots_matched, report.gaps) == (1, 1, 0)


def test_a_wrong_book_is_reported():
    report = verify_rows(
        _tape(
            snapshot("X", 1, yes=[("0.4000", "10.00")]),
            delta("X", 2, "0.4000", "-4.00", "yes"),
            snapshot("X", 3, yes=[("0.4000", "7.00")]),
        )
    )
    assert not report.ok
    assert report.mismatches == ["X"]


def test_after_a_gap_the_snapshot_is_a_resync_not_a_match():
    report = verify_rows(
        _tape(
            snapshot("X", 1, yes=[("0.4000", "10.00")]),
            delta("X", 3, "0.4000", "-4.00", "yes"),  # seq 2 missing
            snapshot("X", 4, yes=[("0.4000", "1.00")]),
        )
    )
    assert report.gaps == 1
    assert report.resyncs == 1
    assert report.snapshots_compared == 0
    assert not report.ok  # nothing clean was proven


def test_legacy_no_leg_pricing_is_converted_to_yes_asks():
    # NO bid at 60c == YES ask at 40c... and a matching snapshot must agree level for level
    report = verify_rows(
        _tape(
            snapshot("X", 1, no=[("0.6000", "5.00")]),
            delta("X", 2, "0.6000", "1.00", "no"),
            snapshot("X", 3, no=[("0.6000", "6.00")]),
            use_yes_price=False,
        )
    )
    assert report.ok


def test_impossible_delta_counts_as_negative_level_and_dirties_the_book():
    report = verify_rows(
        _tape(
            snapshot("X", 1, yes=[("0.4000", "1.00")]),
            delta("X", 2, "0.4000", "-2.00", "yes"),
            snapshot("X", 3, yes=[("0.4000", "1.00")]),
        )
    )
    assert report.negative_levels == 1
    assert report.resyncs == 1


def test_delta_before_any_snapshot_is_an_orphan():
    report = verify_rows(_tape(delta("Y", 1, "0.4000", "1.00", "yes")))
    assert report.orphan_deltas == 1


def test_reconnect_resets_sequences_and_dirties_books():
    rows = _tape(snapshot("X", 1, yes=[("0.4000", "1.00")]))
    rows += [("ladderbook", note("connected")), ("kalshi", subscribed("orderbook_delta", 7))]
    rows += [("kalshi", snapshot("X", 1, yes=[("0.5000", "1.00")], sid=7))]
    report = verify_rows(rows)
    assert report.gaps == 0
    assert report.resyncs == 1
