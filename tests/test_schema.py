from orderflow_mm.schema import event_id, parse_message


def test_parse_book_ticker() -> None:
    message = {
        "stream": "btcusdt@bookTicker",
        "data": {"u": 12, "s": "BTCUSDT", "b": "100.0", "B": "2", "a": "100.1", "A": "3"},
    }
    stream, row = parse_message(message, receive_ts_ns=123)
    assert stream == "book_ticker"
    assert row["bid_price"] == 100.0
    assert row["ask_qty"] == 3.0
    assert event_id(stream, row) == 12


def test_parse_trade() -> None:
    message = {
        "stream": "btcusdt@trade",
        "data": {
            "E": 1000,
            "T": 999,
            "t": 42,
            "s": "BTCUSDT",
            "p": "100.05",
            "q": "0.01",
            "m": True,
        },
    }
    stream, row = parse_message(message, receive_ts_ns=123)
    assert stream == "trade"
    assert row["buyer_is_maker"] is True
    assert event_id(stream, row) == 42
