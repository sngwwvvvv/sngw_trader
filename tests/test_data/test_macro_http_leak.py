from pathlib import Path

FORBIDDEN = ("yfinance", "api.stlouisfed.org", "FRED_API_KEY")
ALLOW_DATA = True


def test_non_data_modules_do_not_call_macro_http() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "sngw_trader"
    for folder in ("strategies", "indicators", "runners", "research"):
        for path in (root / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for tok in FORBIDDEN:
                assert tok not in text, f"{path} contains {tok}"
