# sngw_trader

NautilusTrader가 러너이고, OKX가 유일한 라이브 거래소이고, catalog는 US ETF 일봉을 받을 수 있다.

바이브 코딩 전에 읽을 것:

- `NAUTILUS_VIBE_RULES.md` — 고정 규칙
- `AGENTS.md` — 에이전트 진입점

## 설치

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

## 역할

| 경로                        | 하는 일                          | 하지 않는 일        |
| --------------------------- | -------------------------------- | ------------------- |
| `src/sngw_trader/strategies/` | 시그널, `submit_order`             | 노드 조립, OKX REST |
| `src/sngw_trader/runners/`    | `BacktestNode` / `TradingNode` 조립 | 매매 조건           |
| `src/sngw_trader/data/`       | catalog 적재                       | 주문                |

## 실행 순서

1. catalog에 데이터 적재 (`scripts` / `sngw_trader.data`)
2. `python -m sngw_trader.runners.backtest_okx`
3. OKX 데모 키를 `.env`에 넣고 `OKX_ENV=demo`
4. `python -m sngw_trader.runners.live_okx`
5. 실거래는 `OKX_ENV=live` **그리고** `CONFIRM_LIVE=YES`

기본값은 DEMO다. `CONFIRM_LIVE` 없이 실주문이 나가지 않는다.

## OI 평균회귀 백테스트

```powershell
$env:CATALOG_START="2026-01-01T00:00:00+00:00"
$env:CATALOG_END="2026-01-02T00:00:00+00:00"
$env:OI_ENABLED="true"
uv run catalog-download

$env:OI_STRATEGY="oi_a"; uv run python -m sngw_trader.runners.backtest_oi
$env:OI_STRATEGY="oi_b"; uv run python -m sngw_trader.runners.backtest_oi
```

## 설치된 Nautilus 심볼 확인

버전마다 factory 이름이 다를 수 있다.

```bash
python -c "import nautilus_trader.adapters.okx as m; print(dir(m))"
python -c "from nautilus_trader.live.node import TradingNode; print(TradingNode)"
```
