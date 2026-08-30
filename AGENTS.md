# AGENTS

이 저장소의 에이전트(Cursor, Claude Code, Codex 등)는  
**`NAUTILUS_VIBE_RULES.md`를 항상 적용한다.**

한 줄 요약:

- 러너는 만들지 않는다. `BacktestNode` / `TradingNode`가 러너다.
- 거래소는 OKX만. `InstrumentId`는 `*.OKX`.
- 전략은 `nautilus_trader.trading.Strategy` 한 클래스.
- 백테스트는 BacktestNode + ParquetDataCatalog.
- 라이브는 TradingNode + OKX data/exec factory.
- 전략 파일은 노드를 조립하지 않고, 러너 파일은 매매 조건을 갖지 않는다.
- 시크릿은 env. 기본 실행 모드는 OKX DEMO.
- 자체 루프, `ccxt`, `python-okx` 직접 주문 금지.

자세한 금지 항목, 레이아웃, OKX 규칙, 작업 순서는 `NAUTILUS_VIBE_RULES.md`를 따른다.
