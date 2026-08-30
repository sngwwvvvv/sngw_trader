from pathlib import Path


FORBIDDEN = (
    "ccxt",
    "python_okx",
    "okx.Trade",
    "requests.get",
    "httpx.",
    "websocket",
)


def test_strategy_module_has_no_exchange_io() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "sngw_trader" / "strategies"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            assert token not in text, f"{path} contains forbidden token {token}"
        assert "LiveNode" not in text
        assert "BacktestNode" not in text
        assert "OKXDataClientFactory" not in text
