# Dynamic Grid Trading (DGT) BTC Perp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 논문 DGT(등비 그리드 + 동적 리셋)를 `BTC-USDT-SWAP.OKX` 롱온리 전략으로 구현하고, 기존 walk-forward가 24칸 JSON에서 equity Sharpe로 IS를 고르며 펀딩 제외/포함과 MC-skip을 리포트하게 한다.

**Architecture:** 그리드 수학과 백/워킹 장부는 `dynamic_grid.py`의 순수 함수 + `GridBook`에 둔다. `DynamicGrid`는 완성 1분봉/체결 이벤트를 장부에 넣고 `order_factory.limit` / `market`만 낸다. 리서치 변경은 `GridSpec.select`로 옵트인한다. `select`가 없거나 `None`이면 기존 거래 Sharpe + 거래 iid MC를 유지한다.

**Tech Stack:** Python 3.12+, NautilusTrader `Strategy` / `StrategyConfig` / `BacktestNode` / `ImportableStrategyConfig`, 기존 `build_run_config()` venue 모델, pytest, Decimal.

**Spec:** `docs/superpowers/specs/2026-09-13-dgt-btc-perp-design.md`

## Global Constraints

- 러너는 만들지 않는다. `BacktestNode` + 기존 `build_run_config()`만 사용. `TradingNode` 연결은 범위 밖.
- 거래소는 OKX만. `InstrumentId`는 `*.OKX`. BarType은 `{id}-1-MINUTE-LAST-EXTERNAL`.
- 전략 파일은 노드/OKX adapter/`ccxt`/`python-okx`/네트워크를 import하지 않는다.
- `strategy_factory.py`에 DGT를 넣지 않는다. 본평가는 `ImportableStrategyConfig` + `WF_GRID_PATH`.
- 전역 `WalkForwardConfig` 기본값(6/3/6, min_trades, `build_wf_configs`의 `WF_WARMUP_DAYS` 기본 61)은 바꾸지 않는다. DGT 실행 시에만 env `WF_GRID_PATH=research/grids/dgt_btc.json`, `WF_WARMUP_DAYS=1`.
- 사이징 vol targeting / ATR 스톱 없음. 논문 8bps / 등차 그리드 / 숏 / ETH 없음.
- 엔진에 펀딩을 넣지 않는다. 펀딩은 `data/funding.py` 후처리.
- 창 종료 시 백 강제 청산 없음 (`close_positions_on_stop=False`).
- 단위 테스트는 합성 데이터, 네트워크 없음. 노드를 띄우는 smoke는 작성하지 않는다.
- TDD: 각 행동마다 실패 테스트 → 구현 → 통과. 실행은 저장소 루트에서 `uv run pytest <path> -v`.
- 베이스라인: 현재 워크스페이스의 `compute_equity_metrics` / `extract_analyzer_equity_marks` / `RunResult.equity_marks`를 전제로 한다. equity 지표를 다시 만들지 않는다.
- 커밋 접두: 전략 태스크는 `feat(dgt):`, 리서치 태스크는 `feat(research):`.

## File map

| File | Role |
|---|---|
| `src/sngw_trader/strategies/dynamic_grid.py` | Create: 순수 그리드 수학, `GridBook`, `DynamicGridConfig`, `DynamicGrid` |
| `tests/test_strategies/test_dynamic_grid.py` | Create: 스펙 §8 단위 테스트 |
| `research/grids/dgt_btc.json` | Create: 24칸 + `select: equity_sharpe` |
| `src/sngw_trader/research/config.py` | Modify: `GridSpec.select: str \| None = None` |
| `src/sngw_trader/research/walk_forward.py` | Modify: `load_grid` select, equity Sharpe 분기, MC skip, 펀딩 병기, `WF_ONESHOT` |
| `src/sngw_trader/research/executor.py` | Modify: `RunResult.fills`, `extract_fills` |
| `src/sngw_trader/data/funding.py` | Modify: `parse_fills` 공개, `apply_funding_to_marks` |
| `tests/test_research/test_walk_forward.py` | Modify: select / oneshot / MC-null |
| `tests/test_research/test_executor.py` | Modify: fills 추출 |
| `tests/test_data/test_funding.py` | Modify: marks 보정 |

건드리지 않음: `strategy_factory.py`, `runners/live_okx.py`, 다른 전략 JSON, Wiki, vault lifecycle.

---

### Task 1: Geometric levels + quantization

**Files:**
- Create: `src/sngw_trader/strategies/dynamic_grid.py`
- Test: `tests/test_strategies/test_dynamic_grid.py`

**Interfaces:**
- Consumes: `decimal.Decimal` only (이 태스크에서는 Nautilus Strategy 없음)
- Produces:
  - `geometric_levels(center: Decimal, k: Decimal, h: int) -> list[Decimal]` — `i = -h .. +h` 오름차순, `level(i) = center * (1+k)**i`
  - `quantize_price(instrument, value: Decimal)` — `instrument.make_price` 결과가 0이면 `None`
  - `quantize_qty(instrument, value: Decimal)` — `instrument.make_qty` 결과가 0이거나 `ValueError`이면 `None`

- [ ] **Step 1: Write the failing test**

`tests/test_strategies/test_dynamic_grid.py`:

```python
from decimal import Decimal, ROUND_DOWN

import pytest

from sngw_trader.strategies.dynamic_grid import (
    geometric_levels,
    quantize_price,
    quantize_qty,
)


def test_geometric_levels_center_bounds_and_count():
    p = Decimal("100")
    k = Decimal("0.01")
    h = 3
    levels = geometric_levels(p, k, h)
    assert len(levels) == 7
    assert levels[h] == p
    assert levels[0] == p * (1 + k) ** (-h)
    assert levels[-1] == p * (1 + k) ** h
    for i, lv in enumerate(levels):
        assert lv == p * (1 + k) ** (i - h)


def test_h0_is_single_center():
    assert geometric_levels(Decimal("50"), Decimal("0.02"), 0) == [Decimal("50")]


class _Inst:
    def make_price(self, value):
        d = Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_DOWN)
        if d == 0:
            raise ValueError("zero")
        return d

    def make_qty(self, value):
        d = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if d == 0:
            raise ValueError("zero")
        return d


def test_quantize_skips_zero_and_sub_increment():
    inst = _Inst()
    assert quantize_price(inst, Decimal("123.49")) == Decimal("123.4")
    assert quantize_qty(inst, Decimal("0.019")) == Decimal("0.01")
    assert quantize_qty(inst, Decimal("0.003")) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_strategies/test_dynamic_grid.py -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `sngw_trader.strategies.dynamic_grid`

- [ ] **Step 3: Write minimal implementation**

`src/sngw_trader/strategies/dynamic_grid.py`:

```python
"""Dynamic Grid Trading. Strategy + Config only. No node / OKX adapter."""

from __future__ import annotations

from decimal import Decimal


def geometric_levels(center: Decimal, k: Decimal, h: int) -> list[Decimal]:
    if h < 0:
        raise ValueError("h must be >= 0")
    return [center * (1 + k) ** i for i in range(-h, h + 1)]


def quantize_price(instrument, value: Decimal):
    try:
        px = instrument.make_price(value)
    except ValueError:
        return None
    return None if px == 0 else px


def quantize_qty(instrument, value: Decimal):
    try:
        qty = instrument.make_qty(value)
    except ValueError:
        return None
    return None if qty == 0 else qty
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_strategies/test_dynamic_grid.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/strategies/dynamic_grid.py tests/test_strategies/test_dynamic_grid.py
git commit -m "feat(dgt): add geometric grid levels and quantization"
```

---

### Task 2: Path-consistent ladder increments

**Files:**
- Modify: `src/sngw_trader/strategies/dynamic_grid.py`
- Test: `tests/test_strategies/test_dynamic_grid.py`

**Interfaces:**
- Consumes: Task 1 `Decimal`
- Produces:
  - `sell_increments(remaining_qty: Decimal, n: int) -> list[Decimal]` — `i = 1..n`, `G_i = n-i+1`, `sell_i = remaining / G_i`, 그다음 `remaining -= sell_i`. 합은 입력 `remaining_qty`와 같음
  - `buy_increments(remaining_cash: Decimal, prices: list[Decimal]) -> list[Decimal]` — `j = 1..len(prices)`, `G_j = n-j+1`, `cash_j = remaining_cash / G_j`, `buy_j = cash_j / prices[j-1]`, 그다음 `remaining_cash -= cash_j`. 투입 현금 합은 입력과 같음
  - `sell_ladder(remaining_qty: Decimal, levels_up: list[Decimal]) -> list[tuple[Decimal, Decimal]]` — `[(price, qty), ...]`
  - `buy_ladder(remaining_cash: Decimal, levels_down: list[Decimal]) -> list[tuple[Decimal, Decimal]]`

- [ ] **Step 1: Write the failing test**

`tests/test_strategies/test_dynamic_grid.py`에 추가:

```python
from sngw_trader.strategies.dynamic_grid import (
    buy_increments,
    buy_ladder,
    sell_increments,
    sell_ladder,
)


def test_sell_increments_consume_remaining_exactly():
    remaining = Decimal("6")
    qtys = sell_increments(remaining, 3)
    assert qtys == [Decimal("2"), Decimal("2"), Decimal("2")]
    assert sum(qtys) == remaining


def test_sell_increments_n1_is_all():
    assert sell_increments(Decimal("1.5"), 1) == [Decimal("1.5")]


def test_buy_increments_consume_cash_exactly():
    cash = Decimal("100")
    prices = [Decimal("99"), Decimal("98"), Decimal("97")]
    qtys = buy_increments(cash, prices)
    spent = sum(q * p for q, p in zip(qtys, prices))
    assert spent == cash
    assert qtys[0] == (cash / 3) / prices[0]
    assert qtys[1] == (cash / 3) / prices[1]
    assert qtys[2] == (cash / 3) / prices[2]


def test_ladders_pair_price_and_qty():
    sells = sell_ladder(Decimal("6"), [Decimal("101"), Decimal("102"), Decimal("103")])
    assert sells == [
        (Decimal("101"), Decimal("2")),
        (Decimal("102"), Decimal("2")),
        (Decimal("103"), Decimal("2")),
    ]
    buys = buy_ladder(Decimal("9"), [Decimal("99"), Decimal("98"), Decimal("97")])
    assert len(buys) == 3
    assert buys[0][0] == Decimal("99")
    assert sum(q * p for p, q in buys) == Decimal("9")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_strategies/test_dynamic_grid.py::test_sell_increments_consume_remaining_exactly -v`
Expected: FAIL with `ImportError` for `sell_increments`

- [ ] **Step 3: Write minimal implementation**

`dynamic_grid.py`에 추가:

```python
def sell_increments(remaining_qty: Decimal, n: int) -> list[Decimal]:
    if n <= 0:
        return []
    out: list[Decimal] = []
    rem = remaining_qty
    for i in range(1, n + 1):
        g = n - i + 1
        q = rem / g
        out.append(q)
        rem -= q
    return out


def buy_increments(remaining_cash: Decimal, prices: list[Decimal]) -> list[Decimal]:
    n = len(prices)
    if n == 0:
        return []
    out: list[Decimal] = []
    rem = remaining_cash
    for j, px in enumerate(prices, 1):
        g = n - j + 1
        cash_j = rem / g
        out.append(cash_j / px)
        rem -= cash_j
    return out


def sell_ladder(remaining_qty: Decimal, levels_up: list[Decimal]) -> list[tuple[Decimal, Decimal]]:
    return list(zip(levels_up, sell_increments(remaining_qty, len(levels_up))))


def buy_ladder(remaining_cash: Decimal, levels_down: list[Decimal]) -> list[tuple[Decimal, Decimal]]:
    return list(zip(levels_down, buy_increments(remaining_cash, levels_down)))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_strategies/test_dynamic_grid.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/strategies/dynamic_grid.py tests/test_strategies/test_dynamic_grid.py
git commit -m "feat(dgt): add path-consistent buy/sell ladder increments"
```

---

### Task 3: GridBook inventory and reset rules

**Files:**
- Modify: `src/sngw_trader/strategies/dynamic_grid.py`
- Test: `tests/test_strategies/test_dynamic_grid.py`

**Interfaces:**
- Consumes: Task 1 `geometric_levels`, Task 2 `sell_ladder` / `buy_ladder`
- Produces: `GridBook` with
  - fields: `h: int`, `k: Decimal`, `reset_enabled: bool`, `bag_qty: Decimal`, `grid_qty: Decimal`, `working_cash: Decimal`, `center: Decimal | None`, `levels: tuple[Decimal, ...]`, `active: bool`, `stopped: bool`
  - `lower` / `upper` properties
  - `open_grid(M: Decimal, price: Decimal, min_qty: Decimal) -> bool` — `qty = (M/2)/price`. `qty < min_qty`이면 False, 상태 불변. 성공 시 `grid_qty=qty`, `working_cash=M/2`, `center=price`, levels 계산, `active=True`
  - `apply_buy(qty, price)` — `grid_qty += qty`, `working_cash -= qty*price` (음수면 0). `bag_qty` 불변
  - `apply_sell(qty, price)` — `qty`는 `grid_qty`로 클립. `bag_qty` 불변. `working_cash += filled*price`
  - `working_orders(last_price: Decimal) -> tuple[list[tuple[Decimal, Decimal]], list[tuple[Decimal, Decimal]]]` — `(sells, buys)`. sell은 `level(+i) > last_price`, buy는 `level(-j) < last_price`. 증분은 **남은 칸 수**로 다시 계산
  - `should_reset_upper(price)` — `active`이고 (`price > upper` 또는 `grid_qty == 0`)
  - `should_reset_lower(price)` — `active`이고 (`price < lower` 또는 `working_cash == 0`)
  - `reset_upper() -> None` — `grid_qty=0`, `active=False`. `reset_enabled=False`면 `stopped=True`. `bag_qty` 유지
  - `reset_lower() -> None` — `bag_qty += grid_qty`, `grid_qty=0`, `working_cash=0`, `active=False`. `reset_enabled=False`면 `stopped=True`
  - `can_open() -> bool` — `not stopped`

- [ ] **Step 1: Write the failing test**

```python
from sngw_trader.strategies.dynamic_grid import GridBook


def _book(**kw) -> GridBook:
    return GridBook(h=3, k=Decimal("0.01"), reset_enabled=True, **kw)


def test_open_grid_splits_cash_and_sets_levels():
    b = _book()
    assert b.open_grid(Decimal("10000"), Decimal("100"), Decimal("0.01")) is True
    assert b.grid_qty == Decimal("50")          # (10000/2)/100
    assert b.working_cash == Decimal("5000")
    assert b.bag_qty == 0
    assert b.center == Decimal("100")
    assert len(b.levels) == 7
    assert b.lower == b.levels[0]
    assert b.upper == b.levels[-1]
    assert b.active is True


def test_open_grid_below_min_qty_is_noop():
    b = _book()
    assert b.open_grid(Decimal("1"), Decimal("100"), Decimal("0.01")) is False
    assert b.active is False
    assert b.grid_qty == 0


def test_working_sell_does_not_reduce_bag():
    b = _book()
    b.open_grid(Decimal("10000"), Decimal("100"), Decimal("0.01"))
    b.bag_qty = Decimal("10")
    b.apply_sell(Decimal("5"), Decimal("101"))
    assert b.bag_qty == Decimal("10")
    assert b.grid_qty == Decimal("45")
    assert b.working_cash == Decimal("5000") + Decimal("5") * Decimal("101")


def test_sell_clips_to_grid_qty_never_short():
    b = _book()
    b.open_grid(Decimal("10000"), Decimal("100"), Decimal("0.01"))
    b.apply_sell(Decimal("999"), Decimal("101"))
    assert b.grid_qty == 0
    assert b.bag_qty == 0


def test_upper_reset_clears_grid_keeps_bag():
    b = _book()
    b.open_grid(Decimal("10000"), Decimal("100"), Decimal("0.01"))
    b.bag_qty = Decimal("7")
    assert b.should_reset_upper(b.upper + Decimal("0.01"))
    b.reset_upper()
    assert b.grid_qty == 0
    assert b.bag_qty == Decimal("7")
    assert b.active is False
    assert b.stopped is False
    assert b.can_open() is True


def test_lower_reset_moves_grid_to_bag():
    b = _book()
    b.open_grid(Decimal("10000"), Decimal("100"), Decimal("0.01"))
    grid = b.grid_qty
    assert b.should_reset_lower(b.lower - Decimal("0.01"))
    b.reset_lower()
    assert b.bag_qty == grid
    assert b.grid_qty == 0
    assert b.working_cash == 0
    assert b.active is False
    assert b.can_open() is True


def test_reset_disabled_stops_after_first_bound():
    b = GridBook(h=3, k=Decimal("0.01"), reset_enabled=False)
    b.open_grid(Decimal("10000"), Decimal("100"), Decimal("0.01"))
    b.reset_upper()
    assert b.stopped is True
    assert b.can_open() is False
    assert b.open_grid(Decimal("5000"), Decimal("110"), Decimal("0.01")) is False
    assert b.active is False


def test_working_orders_only_remaining_side_of_last_price():
    b = _book()
    b.open_grid(Decimal("10000"), Decimal("100"), Decimal("0.01"))
    sells, buys = b.working_orders(Decimal("100"))
    assert len(sells) == 3 and len(buys) == 3
    assert sum(q for _, q in sells) == b.grid_qty
    assert sum(p * q for p, q in buys) == b.working_cash
    sells_up, buys_up = b.working_orders(b.levels[b.h + 1])  # last_price = level(+1)
    assert all(px > b.levels[b.h + 1] for px, _ in sells_up)
    assert len(sells_up) == 2
    assert all(px < b.levels[b.h + 1] for px, _ in buys_up)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_strategies/test_dynamic_grid.py::test_open_grid_splits_cash_and_sets_levels -v`
Expected: FAIL with `ImportError` for `GridBook`

- [ ] **Step 3: Write minimal implementation**

`dynamic_grid.py`에 추가:

```python
from dataclasses import dataclass


@dataclass
class GridBook:
    h: int
    k: Decimal
    reset_enabled: bool
    bag_qty: Decimal = Decimal("0")
    grid_qty: Decimal = Decimal("0")
    working_cash: Decimal = Decimal("0")
    center: Decimal | None = None
    levels: tuple[Decimal, ...] = ()
    active: bool = False
    stopped: bool = False

    @property
    def lower(self) -> Decimal | None:
        return self.levels[0] if self.levels else None

    @property
    def upper(self) -> Decimal | None:
        return self.levels[-1] if self.levels else None

    def can_open(self) -> bool:
        return not self.stopped

    def open_grid(self, m: Decimal, price: Decimal, min_qty: Decimal) -> bool:
        if self.stopped:
            return False
        qty = (m / 2) / price
        if qty < min_qty:
            return False
        self.center = price
        self.levels = tuple(geometric_levels(price, self.k, self.h))
        self.grid_qty = qty
        self.working_cash = m / 2
        self.active = True
        return True

    def apply_buy(self, qty: Decimal, price: Decimal) -> None:
        self.grid_qty += qty
        self.working_cash -= qty * price
        if self.working_cash < 0:
            self.working_cash = Decimal("0")

    def apply_sell(self, qty: Decimal, price: Decimal) -> None:
        filled = qty if qty <= self.grid_qty else self.grid_qty
        self.grid_qty -= filled
        self.working_cash += filled * price

    def working_orders(
        self, last_price: Decimal
    ) -> tuple[list[tuple[Decimal, Decimal]], list[tuple[Decimal, Decimal]]]:
        if not self.active or not self.levels:
            return [], []
        up = [lv for lv in self.levels[self.h + 1 :] if lv > last_price]
        down = [
            self.levels[self.h - i]
            for i in range(1, self.h + 1)
            if self.levels[self.h - i] < last_price
        ]
        return sell_ladder(self.grid_qty, up), buy_ladder(self.working_cash, down)

    def should_reset_upper(self, price: Decimal) -> bool:
        return self.active and self.upper is not None and (price > self.upper or self.grid_qty == 0)

    def should_reset_lower(self, price: Decimal) -> bool:
        return self.active and self.lower is not None and (
            price < self.lower or self.working_cash == 0
        )

    def reset_upper(self) -> None:
        self.grid_qty = Decimal("0")
        self.active = False
        if not self.reset_enabled:
            self.stopped = True

    def reset_lower(self) -> None:
        self.bag_qty += self.grid_qty
        self.grid_qty = Decimal("0")
        self.working_cash = Decimal("0")
        self.active = False
        if not self.reset_enabled:
            self.stopped = True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_strategies/test_dynamic_grid.py -v`
Expected: PASS. `working_orders` 테스트가 실패하면 `down` 순서만 고친다. 새 분기·플래그를 넣지 않는다.

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/strategies/dynamic_grid.py tests/test_strategies/test_dynamic_grid.py
git commit -m "feat(dgt): add GridBook inventory split and reset rules"
```

---

### Task 4: DynamicGridConfig + DynamicGrid adapter

**Files:**
- Modify: `src/sngw_trader/strategies/dynamic_grid.py`
- Test: `tests/test_strategies/test_dynamic_grid.py`

**Interfaces:**
- Consumes: Task 3 `GridBook`, `quantize_qty` / `quantize_price`
- Produces:
  - `DynamicGridConfig(StrategyConfig, frozen=True)` fields: `instrument_id: InstrumentId`, `bar_type: BarType`, `grid_size: float`, `grid_numbers_half: int`, `reset_enabled: bool = True`, `close_positions_on_stop: bool = False`, `use_hyphens_in_client_order_ids: bool = False`, `order_id_tag: str = "DGT"`
  - `DynamicGrid(Strategy)`:
    - `on_start`: `subscribe_bars(bar_type)`
    - `on_stop`: `cancel_all_orders(instrument_id)`. `close_positions_on_stop`이 True일 때만 `close_all_positions`. 기본 False
    - `on_bar`: 완성 봉만. `stopped`면 return. `active`가 아니면 `_try_start(close)`. 활성 그리드면 `should_reset_upper(close)` / `should_reset_lower(close)` 후 `_place_ladder`
    - `on_order_filled`: 해당 instrument만. `_opening`이면 시작 시장가 매수 fill — `apply_buy` 호출 금지(`open_grid`가 이미 `grid_qty`를 넣음). 그 외 BUY → `apply_buy`, SELL → `apply_sell`. `_in_reset`/`_opening`이 아니면 cancel_all + `_place_ladder`
    - `_try_start`: `M =` USDT `free`. 양자화 실패면 return. `open_grid`가 True면 `_opening=True` 후 시장가 매수 `grid_qty` (taker, reduce_only 아님). False면 주문 없음 (재시도 루프 금지, 다음 봉에서 다시 시도는 허용)
    - `_place_ladder`: `working_orders(last_price)` → 양자화 0이면 스킵. SELL LIMIT `reduce_only=True`. BUY LIMIT `reduce_only=False`. 거부 재시도 없음
    - `_reset_upper`: `_in_reset=True`, cancel_all, `grid_qty>0`이면 시장가 매도 `grid_qty` `reduce_only=True` (**`close_all_positions` 금지** — NETTING이라 백까지 팔림). `reset_upper()`. `can_open()`이면 `_try_start(price)`
    - `_reset_lower`: cancel_all, `reset_lower()`, `can_open()`이면 `M=USDT free`로 `_try_start`
    - 현금은 `self.cache.account_for_venue(self.config.instrument_id.venue).balance(USDT).free`
    - 원금 `M`은 config에 두지 않음

- [ ] **Step 1: Write the failing test**

```python
from nautilus_trader.model import BarType, InstrumentId

from sngw_trader.strategies.dynamic_grid import DynamicGrid, DynamicGridConfig


def test_config_defaults_and_okx_ids():
    cfg = DynamicGridConfig(
        instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
        bar_type=BarType.from_str("BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL"),
        grid_size=0.01,
        grid_numbers_half=3,
    )
    assert str(cfg.instrument_id).endswith(".OKX")
    assert "1-MINUTE-LAST-EXTERNAL" in str(cfg.bar_type)
    assert cfg.reset_enabled is True
    assert cfg.close_positions_on_stop is False
    assert cfg.use_hyphens_in_client_order_ids is False
    assert cfg.order_id_tag == "DGT"
    s = DynamicGrid(config=cfg)
    assert s._book.h == 3
    assert s._book.k == Decimal("0.01")
    assert s._book.reset_enabled is True
    assert s._in_reset is False
    assert s._opening is False


def test_strategy_module_has_no_runner_imports():
    from pathlib import Path

    text = Path("src/sngw_trader/strategies/dynamic_grid.py").read_text(encoding="utf-8")
    for token in ("BacktestNode", "TradingNode", "OKXDataClientFactory", "ccxt", "python-okx"):
        assert token not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_strategies/test_dynamic_grid.py::test_config_defaults_and_okx_ids -v`
Expected: FAIL with `ImportError` for `DynamicGridConfig`

- [ ] **Step 3: Write minimal implementation**

`dynamic_grid.py` 상단 import와 클래스 추가. 기존 순수 함수/`GridBook`은 유지.

```python
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.objects import Currency
from nautilus_trader.trading import Strategy


class DynamicGridConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    grid_size: float
    grid_numbers_half: int
    reset_enabled: bool = True
    close_positions_on_stop: bool = False
    use_hyphens_in_client_order_ids: bool = False
    order_id_tag: str = "DGT"


class DynamicGrid(Strategy):
    def __init__(self, config: DynamicGridConfig) -> None:
        super().__init__(config)
        self._book = GridBook(
            h=config.grid_numbers_half,
            k=Decimal(str(config.grid_size)),
            reset_enabled=config.reset_enabled,
        )
        self._in_reset = False
        self._opening = False
        self._last_price: Decimal | None = None

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        if self.config.close_positions_on_stop:
            self.close_all_positions(self.config.instrument_id)

    def on_bar(self, bar: Bar) -> None:
        if bar.bar_type != self.config.bar_type:
            return
        price = Decimal(str(bar.close))
        self._last_price = price
        if self._book.stopped:
            return
        if not self._book.active:
            self._try_start(price)
            return
        if self._book.should_reset_upper(price):
            self._reset_upper(price)
            return
        if self._book.should_reset_lower(price):
            self._reset_lower(price)
            return
        self._place_ladder()

    def on_order_filled(self, event: OrderFilled) -> None:
        if event.instrument_id != self.config.instrument_id:
            return
        qty = event.last_qty.as_decimal()
        px = event.last_px.as_decimal()
        if self._opening:
            self._opening = False
            if self._book.active and not self._book.stopped:
                self._place_ladder()
            return
        if event.order_side == OrderSide.BUY:
            self._book.apply_buy(qty, px)
        else:
            self._book.apply_sell(qty, px)
        if self._in_reset or self._book.stopped or not self._book.active:
            return
        self.cancel_all_orders(self.config.instrument_id)
        self._place_ladder()

    def _free_usdt(self) -> Decimal:
        account = self.cache.account_for_venue(self.config.instrument_id.venue)
        if account is None:
            return Decimal("0")
        bal = account.balance(Currency.from_str("USDT"))
        if bal is None:
            return Decimal("0")
        return bal.free.as_decimal()

    def _instrument(self):
        return self.cache.instrument(self.config.instrument_id)

    def _min_qty(self, instrument) -> Decimal:
        raw = getattr(instrument, "min_quantity", None)
        if raw is None:
            return Decimal("0")
        return raw.as_decimal() if hasattr(raw, "as_decimal") else Decimal(str(raw))

    def _try_start(self, price: Decimal) -> None:
        instrument = self._instrument()
        if instrument is None:
            self.log.error(f"Instrument not in cache: {self.config.instrument_id}")
            return
        m = self._free_usdt()
        min_qty = self._min_qty(instrument)
        probe = (m / 2) / price if price != 0 else Decimal("0")
        q = quantize_qty(instrument, probe)
        if q is None:
            return
        if not self._book.open_grid(m, price, min_qty):
            return
        self._opening = True
        self.submit_order(
            self.order_factory.market(self.config.instrument_id, OrderSide.BUY, q)
        )

    def _place_ladder(self) -> None:
        instrument = self._instrument()
        if instrument is None or self._last_price is None or not self._book.active:
            return
        sells, buys = self._book.working_orders(self._last_price)
        for px, qty in sells:
            self._submit_limit(instrument, OrderSide.SELL, px, qty, reduce_only=True)
        for px, qty in buys:
            self._submit_limit(instrument, OrderSide.BUY, px, qty, reduce_only=False)

    def _submit_limit(self, instrument, side: OrderSide, px: Decimal, qty: Decimal, *, reduce_only: bool) -> None:
        price = quantize_price(instrument, px)
        q = quantize_qty(instrument, qty)
        if price is None or q is None:
            return
        self.submit_order(
            self.order_factory.limit(
                instrument_id=self.config.instrument_id,
                order_side=side,
                quantity=q,
                price=price,
                time_in_force=TimeInForce.GTC,
                reduce_only=reduce_only,
            )
        )

    def _reset_upper(self, price: Decimal) -> None:
        self._in_reset = True
        try:
            self.cancel_all_orders(self.config.instrument_id)
            instrument = self._instrument()
            if instrument is not None and self._book.grid_qty > 0:
                q = quantize_qty(instrument, self._book.grid_qty)
                if q is not None:
                    self.submit_order(
                        self.order_factory.market(
                            self.config.instrument_id,
                            OrderSide.SELL,
                            q,
                            reduce_only=True,
                        )
                    )
            self._book.reset_upper()
            if self._book.can_open():
                self._try_start(price)
        finally:
            self._in_reset = False

    def _reset_lower(self, price: Decimal) -> None:
        self._in_reset = True
        try:
            self.cancel_all_orders(self.config.instrument_id)
            self._book.reset_lower()
            if self._book.can_open():
                self._try_start(price)
        finally:
            self._in_reset = False
```

시작 매수 fill 전에 `_place_ladder`가 호출되지 않게, `on_bar`는 `_try_start` 후 return하고 래더는 `on_order_filled`가 올린다. `_opening` 동안 `apply_buy`를 부르면 `open_grid`가 넣은 `grid_qty`와 이중 계산된다.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_strategies/test_dynamic_grid.py tests/test_strategies/test_no_exchange_io.py -v`
Expected: PASS. `use_hyphens_in_client_order_ids`를 서브클래스에 재선언해서 msgspec이 거부하면, 필드 선언을 제거하고 생성 테스트에서 `DynamicGridConfig(..., use_hyphens_in_client_order_ids=False, order_id_tag="DGT")`를 넘긴다. 기본값이 True로 남으면 `Config`에 다시 명시한다 — OKX 32자/하이픈 금지는 스펙 필수.

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/strategies/dynamic_grid.py tests/test_strategies/test_dynamic_grid.py
git commit -m "feat(dgt): add DynamicGrid strategy adapter"
```

---

### Task 5: Grid JSON + GridSpec.select + equity Sharpe IS pick

**Files:**
- Create: `research/grids/dgt_btc.json`
- Modify: `src/sngw_trader/research/config.py`
- Modify: `src/sngw_trader/research/walk_forward.py`
- Test: `tests/test_research/test_walk_forward.py`

**Interfaces:**
- Consumes: 기존 `select_best(grid, sharpe_by_key, trades_by_key, min_trades)`, `compute_equity_metrics(marks, initial)`, `RunResult.equity_marks`
- Produces:
  - `GridSpec(..., select: str | None = None)` — 기존 4필드 생성자는 그대로 동작
  - `load_grid()`가 JSON `select`를 읽음. 없거나 null이면 `None` (거래 Sharpe). 값이 있고 `equity_sharpe`가 아니면 `ValueError`
  - `_run_is_grid` / `main` 선택 분기: `spec.select == "equity_sharpe"`이면 점수 = `compute_equity_metrics(...).sharpe`, 게이트 = `len(equity_marks) >= 2`. metrics가 `None`이거나 `sharpe is None`이면 탈락 (`gate=0`). `wf_cfg.min_trades`를 이 분기에 쓰지 않음
  - 그 외는 기존: `sharpe_from_trades(trade_returns)` + `n_trades >= wf_cfg.min_trades`
  - `oos_is_sharpe_ratio`는 거래 경로 유지. equity 경로는 IS/OOS equity Sharpe 비. IS equity Sharpe `<= 0` 또는 OOS Sharpe `None`이면 ratio `None`
  - `research/grids/dgt_btc.json` 24칸. `strategy_factory` 변경 없음

- [ ] **Step 1: Write the failing tests**

`tests/test_research/test_walk_forward.py`에 추가:

```python
from sngw_trader.research.config import GridSpec


def test_load_grid_reads_select(tmp_path, monkeypatch):
    p = tmp_path / "grid.json"
    p.write_text(json.dumps({
        "strategy_path": "sngw_trader.strategies.dynamic_grid:DynamicGrid",
        "config_path": "sngw_trader.strategies.dynamic_grid:DynamicGridConfig",
        "select": "equity_sharpe",
        "fixed": {},
        "grid": {"grid_size": [0.01], "reset_enabled": [True]},
    }))
    monkeypatch.setenv("WF_GRID_PATH", str(p))
    spec = load_grid()
    assert spec.select == "equity_sharpe"
    assert spec.grid["grid_size"] == [0.01]


def test_load_grid_json_without_select_is_none(tmp_path, monkeypatch):
    p = tmp_path / "grid.json"
    p.write_text(json.dumps({
        "strategy_path": "sngw_trader.strategies.example.ema_cross:EMACross",
        "config_path": "sngw_trader.strategies.example.ema_cross:EMACrossConfig",
        "fixed": {"trade_size": "0.02"},
        "grid": {"fast_ema_period": [5, 10]},
    }))
    monkeypatch.setenv("WF_GRID_PATH", str(p))
    assert load_grid().select is None


def test_load_grid_rejects_unknown_select(tmp_path, monkeypatch):
    p = tmp_path / "grid.json"
    p.write_text(json.dumps({
        "strategy_path": "x:Y",
        "config_path": "x:C",
        "select": "trade_sharpe",
        "fixed": {},
        "grid": {"a": [1]},
    }))
    monkeypatch.setenv("WF_GRID_PATH", str(p))
    with pytest.raises(ValueError, match="equity_sharpe"):
        load_grid()


def test_main_selects_equity_sharpe_not_trade(monkeypatch, tmp_path):
    import sngw_trader.research.walk_forward as wf

    p = tmp_path / "dgt.json"
    p.write_text(json.dumps({
        "strategy_path": "sngw_trader.strategies.dynamic_grid:DynamicGrid",
        "config_path": "sngw_trader.strategies.dynamic_grid:DynamicGridConfig",
        "select": "equity_sharpe",
        "fixed": {},
        "grid": {"grid_size": [0.01, 0.02]},
    }))
    monkeypatch.setenv("WF_GRID_PATH", str(p))
    _wf_env(monkeypatch)

    def fake_run(*a, **k):
        params = k.get("params") or (a[4] if len(a) > 4 else {})
        if params.get("grid_size") == 0.01:
            return RunResult(
                [100.0, 100.0], [0.5, 0.5], 2, 200.0,
                equity_marks=[(0, 10000.0), (NS_PER_YEAR, 0.0)],
            )
        return RunResult(
            [1.0, -2.0], [0.01, -0.02], 2, -1.0,
            equity_marks=[(0, 10000.0), (NS_PER_YEAR, 12000.0)],
        )

    monkeypatch.setattr(wf, "run_window", fake_run)
    monkeypatch.setattr(wf, "bootstrap_trades", lambda trades, mc_cfg: {"sims": mc_cfg.n_sims})
    monkeypatch.setattr(report, "run_dir", lambda name: tmp_path)
    monkeypatch.setattr(report, "print_summary", lambda summary: None)
    wf.main()
    wf_report = json.loads((tmp_path / "wf_report.json").read_text(encoding="utf-8"))
    assert wf_report["windows"]
    for w in wf_report["windows"]:
        assert w["selected"] == {"grid_size": 0.02}
```

기존 `test_load_grid_from_json` 끝에 `assert spec.select is None`을 추가한다.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_research/test_walk_forward.py::test_load_grid_reads_select tests/test_research/test_walk_forward.py::test_main_selects_equity_sharpe_not_trade -v`
Expected: FAIL (`GridSpec` has no `select` and/or selected stays trade-Sharpe winner `0.01`)

- [ ] **Step 3: Write minimal implementation**

`src/sngw_trader/research/config.py` — `GridSpec` 마지막 필드:

```python
@dataclass(frozen=True)
class GridSpec:
    strategy_path: str
    config_path: str
    fixed: dict[str, object]
    grid: dict[str, list]
    select: str | None = None
```

`walk_forward.py` `load_grid` JSON 분기:

```python
select = data.get("select")
if select is not None and select != "equity_sharpe":
    raise ValueError(f"GridSpec.select must be absent or 'equity_sharpe', got {select!r}")
return GridSpec(
    strategy_path=data["strategy_path"],
    config_path=data["config_path"],
    fixed=data.get("fixed", {}),
    grid=data["grid"],
    select=select,
)
```

DEFAULT_GRID / env sizing 분기는 `select`를 넘기지 않는다 (`None`).

`walk_forward.py`에 헬퍼:

```python
def _selection_inputs(
    spec: GridSpec,
    results: dict[tuple, RunResult],
    initial_capital: float,
    min_trades: int,
) -> tuple[dict[tuple, float], dict[tuple, int], int]:
    sharpes: dict[tuple, float] = {}
    gates: dict[tuple, int] = {}
    if spec.select == "equity_sharpe":
        for key, res in results.items():
            metrics = compute_equity_metrics(res.equity_marks, initial_capital)
            if metrics is None or metrics["sharpe"] is None:
                sharpes[key] = 0.0
                gates[key] = 0
            else:
                sharpes[key] = metrics["sharpe"]
                gates[key] = len(res.equity_marks)
        return sharpes, gates, 2
    for key, res in results.items():
        sharpes[key] = sharpe_from_trades(res.trade_returns)
        gates[key] = res.n_trades
    return sharpes, gates, min_trades
```

`_run_is_grid`는 `results`만 반환한다. 내부에서 `sharpe_from_trades`를 계산하지 않는다.

```python
def _run_is_grid(...) -> dict[tuple, RunResult]:
    catalog_path = str(settings.catalog_path)
    instrument_id = settings.instrument_id_str
    axes = sorted(spec.grid)
    results: dict[tuple, RunResult] = {}
    keys = param_keys(spec.grid)
    for i, key in enumerate(keys, 1):
        res = run_window(
            catalog_path, instrument_id, settings=settings, spec=spec,
            params=dict(zip(axes, key)),
            start=window.is_start, end=window.is_end,
            warmup_days=wf_cfg.warmup_days,
        )
        results[key] = res
        print(f"[wf] window {window.index} IS {i}/{len(keys)} {dict(zip(axes, key))} trades={res.n_trades}")
    return results
```

`main` 선택 블록 (`grid_results, sharpes = _run_is_grid(...)` 를 아래로 교체):

```python
grid_results = _run_is_grid(settings, spec, window, wf_cfg)
sharpes, gates, min_gate = _selection_inputs(
    spec, grid_results, mc_cfg.initial_capital, wf_cfg.min_trades,
)
best = select_best(spec.grid, sharpes, gates, min_gate)
```

`main`의 `oos_is_sharpe_ratio` 블록:

```python
if best is not None and oos_result is not None:
    if spec.select == "equity_sharpe":
        oos_s = None if oos_metrics is None or oos_metrics["equity"] is None else oos_metrics["equity"]["sharpe"]
        ratio = None if sharpes[best] <= 0 or oos_s is None else oos_s / sharpes[best]
    else:
        ratio = oos_is_sharpe_ratio(sharpes[best], oos_result)
else:
    ratio = None
```

`research/grids/dgt_btc.json`:

```json
{
  "strategy_path": "sngw_trader.strategies.dynamic_grid:DynamicGrid",
  "config_path": "sngw_trader.strategies.dynamic_grid:DynamicGridConfig",
  "select": "equity_sharpe",
  "fixed": {},
  "grid": {
    "grid_size": [0.005, 0.01, 0.02, 0.05],
    "grid_numbers_half": [3, 5, 10],
    "reset_enabled": [true, false]
  }
}
```

칸 수 = 4 × 3 × 2 = 24.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_research/test_walk_forward.py tests/test_research/test_executor.py tests/test_research/test_param_scan.py -v`
Expected: PASS. `GridSpec`에 필드가 추가돼도 기존 키워드 생성자는 통과해야 한다.

- [ ] **Step 5: Commit**

```bash
git add research/grids/dgt_btc.json src/sngw_trader/research/config.py src/sngw_trader/research/walk_forward.py tests/test_research/test_walk_forward.py
git commit -m "feat(research): select IS params by equity Sharpe for DGT"
```

---

### Task 6: Fills extract + funding pair + MC skip + assumptions

**Files:**
- Modify: `src/sngw_trader/research/executor.py`
- Modify: `src/sngw_trader/data/funding.py`
- Modify: `src/sngw_trader/research/walk_forward.py`
- Test: `tests/test_research/test_executor.py`, `tests/test_data/test_funding.py`, `tests/test_research/test_walk_forward.py`

**Interfaces:**
- Consumes: Task 5 `spec.select`, 기존 `funding_cost`, `generate_order_fills_report()`
- Produces:
  - `RunResult.fills: list[tuple[int, float, float]]` — `(ts_ns, signed_qty, price)`, 기본 `[]`
  - `parse_fills(rows: list[dict]) -> list[tuple[int, float, float]]` — 기존 `_parse_fills`를 공개 이름로 유지(본문 동일). `_parse_fills = parse_fills` 별칭 가능
  - `extract_fills(fills_df, window_start_ns: int) -> list[tuple[int, float, float]]` — `window_start_ns` 미만 제외
  - `run_window`이 fills를 채움
  - `apply_funding_to_marks(marks, fills, rates) -> list[tuple[int, float]]` — 각 mark 시각까지의 `funding_cost`를 equity에서 뺌 (양수 cost = 롱이 지불)
  - `spec.select == "equity_sharpe"`일 때만:
    - `bootstrap_trades` 호출 금지. `mc_report = {"oos": None, "holdout": None, "reason": "trade iid bootstrap is invalid for DGT (position stays open until upper reset)"}`. `summary["mc_oos"]` / `summary["mc_holdout"]` = `None`, `summary["mc_skip_reason"]` = 같은 사유
    - stitched OOS fills + holdout fills로 `fetch_funding_rates("BTC-USDT-SWAP", start_ms, end_ms)` (테스트는 mock). `summary["stitched_oos_funding"]` / `summary["holdout_funding"]` = `{"excluded": equity_metrics, "included": equity_metrics, "funding_cost": float}`
    - `summary["assumptions"]` = 스펙 §9 가정 5줄을 그대로
  - `select is None`이면 mc_report / summary 키를 기존과 동일하게 유지 (funding/assumptions/reason 키를 넣지 않음)

- [ ] **Step 1: Write the failing tests**

`tests/test_data/test_funding.py` 상단에 `import pytest`를 추가하고, 아래 테스트를 덧붙인다:

```python
from sngw_trader.data.funding import apply_funding_to_marks


def test_apply_funding_to_marks_subtracts_cost_at_each_ts():
    fills = [(0, 1.0, 100.0)]
    rates = {0: 0.0001}
    marks = [(0, 10000.0), (8 * 3_600_000 * 1_000_000, 10000.0)]
    adjusted = apply_funding_to_marks(marks, fills, rates)
    assert adjusted[0][1] == pytest.approx(10000.0 - 0.01)
    # second mark still includes the t=0 payment
    assert adjusted[1][1] == pytest.approx(10000.0 - 0.01)
```

`tests/test_research/test_executor.py`에 추가:

```python
import pandas as pd

from sngw_trader.research.executor import extract_fills


def test_extract_fills_filters_warmup_and_signs():
    df = pd.DataFrame({
        "ts_event": [50, 150, 200],
        "order_side": ["BUY", "SELL", "BUY"],
        "last_qty": [1.0, 0.4, 0.2],
        "last_px": [100.0, 101.0, 99.0],
    })
    fills = extract_fills(df, window_start_ns=100)
    assert fills == [(150, -0.4, 101.0), (200, 0.2, 99.0)]


def test_extract_fills_empty():
    assert extract_fills(pd.DataFrame(), 0) == []
    assert extract_fills(None, 0) == []


def test_run_result_default_fills():
    r = RunResult([1.0], [0.01], 1, 1.0)
    assert r.fills == []
```

`tests/test_research/test_walk_forward.py`에 추가 (`test_main_selects_equity_sharpe_not_trade`와 같은 JSON/env를 써도 된다):

```python
def test_main_dgt_skips_mc_and_pairs_funding(monkeypatch, tmp_path):
    import sngw_trader.research.walk_forward as wf

    p = tmp_path / "dgt.json"
    p.write_text(json.dumps({
        "strategy_path": "sngw_trader.strategies.dynamic_grid:DynamicGrid",
        "config_path": "sngw_trader.strategies.dynamic_grid:DynamicGridConfig",
        "select": "equity_sharpe",
        "fixed": {},
        "grid": {"grid_size": [0.01]},
    }))
    monkeypatch.setenv("WF_GRID_PATH", str(p))
    _wf_env(monkeypatch)

    filled = RunResult(
        [10.0, -4.0], [0.01, -0.004], 2, 6.0,
        equity_marks=[(0, 10000.0), (NS_PER_YEAR // 2, 10100.0), (NS_PER_YEAR, 9900.0)],
        fills=[(0, 1.0, 100.0)],
    )

    def fake_run(*a, **k):
        return filled

    monkeypatch.setattr(wf, "run_window", fake_run)
    monkeypatch.setattr(
        wf, "fetch_funding_rates",
        lambda inst, start_ms, end_ms: {0: 0.0001},
    )
    monkeypatch.setattr(wf, "bootstrap_trades", lambda *a, **k: (_ for _ in ()).throw(AssertionError("MC must not run")))
    monkeypatch.setattr(report, "run_dir", lambda name: tmp_path)
    monkeypatch.setattr(report, "print_summary", lambda summary: None)
    wf.main()
    mc_report = json.loads((tmp_path / "mc_report.json").read_text(encoding="utf-8"))
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert mc_report["oos"] is None and mc_report["holdout"] is None
    assert "invalid for DGT" in mc_report["reason"]
    assert summary["mc_oos"] is None and summary["mc_holdout"] is None
    assert summary["holdout_funding"]["funding_cost"] == pytest.approx(0.01)
    assert summary["holdout_funding"]["excluded"]["mdd_ratio"] is not None
    assert summary["holdout_funding"]["included"]["mdd_ratio"] is not None
    assert summary["stitched_oos_funding"]["funding_cost"] == pytest.approx(0.01)
    assert "스팟 그리드를 롱온리 퍼프" in summary["assumptions"][0]
    assert len(summary["assumptions"]) == 5
```

Task 5의 `test_main_selects_equity_sharpe_not_trade`에도 `monkeypatch.setattr(wf, "fetch_funding_rates", lambda *a, **k: {0: 0.0001})`를 추가한다. Task 6 이후 이 테스트가 본평가 펀딩 fetch를 타기 때문이다.

기존 `test_main_wires_metrics_into_reports`가 `mc_report == {"oos": ..., "holdout": ...}`로 엄격 비교하므로, 기본 경로에 `reason`을 넣으면 안 된다.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_data/test_funding.py::test_apply_funding_to_marks_subtracts_cost_at_each_ts tests/test_research/test_executor.py::test_extract_fills_filters_warmup_and_signs tests/test_research/test_walk_forward.py::test_main_dgt_skips_mc_and_pairs_funding -v`
Expected: FAIL (함수 없음 / `fills` 필드 없음 / MC가 돌아감)

- [ ] **Step 3: Write minimal implementation**

`funding.py`: `_parse_fills`를 `parse_fills`로 이름 바꾸고 기존 `main`이 `parse_fills`를 호출. 추가:

```python
def apply_funding_to_marks(
    marks: list[tuple[int, float]],
    fills: list[tuple[int, float, float]],
    rates: dict[int, float],
) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    for ts, eq in marks:
        cut = {t: r for t, r in rates.items() if t * 1_000_000 <= ts}
        out.append((ts, eq - funding_cost(fills, cut)))
    return out
```

`executor.py` `RunResult`:

```python
fills: list[tuple[int, float, float]] = field(default_factory=list)
```

```python
from sngw_trader.data.funding import parse_fills


def extract_fills(fills_df, window_start_ns: int) -> list[tuple[int, float, float]]:
    if fills_df is None or getattr(fills_df, "empty", True):
        return []
    fills = parse_fills(fills_df.to_dict("records"))
    return [f for f in fills if f[0] >= window_start_ns]
```

`run_window`의 `return replace(...)` 직전:

```python
fills_df = engine.trader.generate_order_fills_report()
fills = extract_fills(fills_df, window_start_ns)
return replace(result, equity_marks=marks, fills=fills)
```

`walk_forward.py` import:

```python
from sngw_trader.data.funding import apply_funding_to_marks, fetch_funding_rates, funding_cost
```

상수 (모듈 레벨):

```python
DGT_MC_REASON = "trade iid bootstrap is invalid for DGT (position stays open until upper reset)"
DGT_ASSUMPTIONS = [
    "스팟 그리드를 롱온리 퍼프 + 백/워킹 분할로 옮긴 것은 재현이지 동일 전략이 아니다",
    "1분봉 L1 + 확률적 LIMIT 체결은 틱 북이 아니다. 한 봉 다중 체결을 허용한다",
    "중간 입금을 막아 저자 IRR과 분모가 다르다",
    "논문 8bps / Binance spot / 등차 구현을 쓰지 않는다",
    "이 결과만으로 lifecycle을 backtested 이상으로 올리지 않는다",
]
```

헬퍼:

```python
def stitch_fills(window_results: list[RunResult | None]) -> list[tuple[int, float, float]]:
    out: list[tuple[int, float, float]] = []
    for r in window_results:
        if r is not None:
            out.extend(r.fills)
    out.sort(key=lambda f: f[0])
    return out


def stitch_equity(window_results: list[RunResult | None], initial: float) -> list[tuple[int, float]]:
    marks: list[tuple[int, float]] = []
    equity = initial
    first_ts: int | None = None
    for r in window_results:
        if r is None or len(r.equity_marks) < 2:
            continue
        window = r.equity_marks
        if first_ts is None:
            first_ts = window[0][0]
        for i in range(1, len(window)):
            prev, cur = window[i - 1][1], window[i][1]
            if prev <= 0:
                continue
            equity *= cur / prev
            marks.append((window[i][0], equity))
    if first_ts is None or not marks:
        return []
    return [(first_ts, initial)] + marks


def _funding_block(marks, fills, rates, initial: float) -> dict:
    excluded = compute_equity_metrics(marks, initial)
    included = compute_equity_metrics(apply_funding_to_marks(marks, fills, rates), initial)
    return {
        "excluded": excluded,
        "included": included,
        "funding_cost": funding_cost(fills, rates),
    }
```

`main`의 MC / summary 꼬리:

```python
if spec.select == "equity_sharpe":
    mc_oos = None
    mc_holdout = None
    mc_report = {"oos": None, "holdout": None, "reason": DGT_MC_REASON}
    start_ms = int(dt_to_unix_nanos(data_start) / 1_000_000)
    end_ms = int(dt_to_unix_nanos(data_end) / 1_000_000)
    rates = fetch_funding_rates("BTC-USDT-SWAP", start_ms, end_ms)
    oos_fills = stitch_fills(oos_results)
    oos_marks = stitch_equity(oos_results, mc_cfg.initial_capital)
    stitched_funding = _funding_block(oos_marks, oos_fills, rates, mc_cfg.initial_capital)
    holdout_funding = (
        _funding_block(holdout.equity_marks, holdout.fills, rates, mc_cfg.initial_capital)
        if holdout is not None else None
    )
else:
    mc_oos = bootstrap_trades(stitched, mc_cfg) if len(stitched) >= 2 else None
    mc_holdout = bootstrap_trades(holdout.trade_pnls, mc_cfg) if holdout is not None and holdout.n_trades >= 2 else None
    mc_report = {"oos": mc_oos, "holdout": mc_holdout}
    stitched_funding = None
    holdout_funding = None
```

기존 holdout MC 블록을 위 분기로 대체한다. 지금 코드는 `if last_params is not None: holdout = run_window(...); if holdout.n_trades >= 2: mc_holdout = bootstrap_trades(...)`이다. holdout `run_window` 자체는 두 경로 모두 유지하고, bootstrap만 가드한다.

`summary`에 equity 경로일 때만:

```python
if spec.select == "equity_sharpe":
    summary["mc_skip_reason"] = DGT_MC_REASON
    summary["stitched_oos_funding"] = stitched_funding
    summary["holdout_funding"] = holdout_funding
    summary["assumptions"] = DGT_ASSUMPTIONS
```

테스트가 `fetch_funding_rates`를 `wf.fetch_funding_rates`로 패치하므로, `walk_forward`가 함수를 직접 import해야 한다 (`from sngw_trader.data.funding import fetch_funding_rates`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_data/test_funding.py tests/test_research/test_executor.py tests/test_research/test_walk_forward.py -v`
Expected: PASS. 기본 `test_main_wires_metrics_into_reports`의 mc_report 동등 비교가 깨지면 equity 전용 키를 기본 경로에 넣지 않았는지 확인.

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/research/executor.py src/sngw_trader/data/funding.py src/sngw_trader/research/walk_forward.py tests/test_research/test_executor.py tests/test_data/test_funding.py tests/test_research/test_walk_forward.py
git commit -m "feat(research): pair DGT funding metrics and skip trade bootstrap MC"
```

---

### Task 7: WF_ONESHOT diagnostic path

**Files:**
- Modify: `src/sngw_trader/research/walk_forward.py`
- Test: `tests/test_research/test_walk_forward.py`

**Interfaces:**
- Consumes: Task 5 `GridSpec` / `load_grid` / `param_keys` / `_params_for` / `run_window` / `run_metrics` / `bh_metrics`
- Produces:
  - `oneshot_keys(spec: GridSpec) -> list[tuple]` — `reset_enabled` 축이 있으면 값이 `True`인 칸만. 축이 없으면 전체
  - `WF_ONESHOT`이 `1`/`true`/`True`이면 `main`은 창 분할·OOS·holdout·MC bootstrap을 하지 않음. `WF_DATA_START`/`WF_DATA_END` 구간에서 `oneshot_keys`만 `run_window` 1회씩
  - 리포트: `wf_report = {"label": "IS-only", "instrument_id", "data_start", "data_end", "cells": [{"params", "n_trades", "total_pnl", "metrics": {"trade","equity"}, "benchmark"}]}`
  - `mc_report = {"oos": None, "holdout": None, "reason": DGT_MC_REASON}` (oneshot은 진단, MC 없음)
  - `summary = {"label": "IS-only", "strategy", "instrument_id", "n_cells", "cells": 같은 목록, "benchmark": bh_metrics(전체 구간), "assumptions": DGT_ASSUMPTIONS}`
  - 펀딩 병기는 스펙 §6.2 본평가 전용. oneshot에는 funding 키를 넣지 않음
  - `WF_ONESHOT` 미설정 시 기존 `main` 경로 불변

- [ ] **Step 1: Write the failing tests**

```python
from sngw_trader.research.walk_forward import oneshot_keys


def test_oneshot_keys_keeps_reset_true_only():
    spec = GridSpec(
        strategy_path="sngw_trader.strategies.dynamic_grid:DynamicGrid",
        config_path="sngw_trader.strategies.dynamic_grid:DynamicGridConfig",
        fixed={},
        grid={
            "grid_size": [0.01, 0.02],
            "grid_numbers_half": [3],
            "reset_enabled": [True, False],
        },
        select="equity_sharpe",
    )
    keys = oneshot_keys(spec)
    axes = sorted(spec.grid)
    assert len(keys) == 2
    idx = axes.index("reset_enabled")
    assert all(k[idx] is True for k in keys)


def test_oneshot_keys_without_reset_axis_keeps_all():
    spec = GridSpec("x:Y", "x:C", {}, {"grid_size": [0.01, 0.02]})
    assert len(oneshot_keys(spec)) == 2


def test_main_oneshot_skips_windows(monkeypatch, tmp_path):
    import sngw_trader.research.walk_forward as wf

    p = tmp_path / "dgt.json"
    p.write_text(json.dumps({
        "strategy_path": "sngw_trader.strategies.dynamic_grid:DynamicGrid",
        "config_path": "sngw_trader.strategies.dynamic_grid:DynamicGridConfig",
        "select": "equity_sharpe",
        "fixed": {},
        "grid": {
            "grid_size": [0.01, 0.02],
            "reset_enabled": [True, False],
        },
    }))
    monkeypatch.setenv("WF_GRID_PATH", str(p))
    monkeypatch.setenv("WF_ONESHOT", "1")
    _wf_env(monkeypatch)

    calls: list[dict] = []

    def fake_run(*a, **k):
        params = k.get("params") or {}
        calls.append(params)
        return _FAKE_RESULT

    monkeypatch.setattr(wf, "run_window", fake_run)
    monkeypatch.setattr(wf, "bootstrap_trades", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no MC")))
    monkeypatch.setattr(wf, "fetch_funding_rates", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no funding fetch")))
    monkeypatch.setattr(report, "run_dir", lambda name: tmp_path)
    monkeypatch.setattr(report, "print_summary", lambda summary: None)
    wf.main()
    wf_report = json.loads((tmp_path / "wf_report.json").read_text(encoding="utf-8"))
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert wf_report["label"] == "IS-only"
    assert "windows" not in wf_report
    assert len(calls) == 2
    assert all(c["reset_enabled"] is True for c in calls)
    assert {c["grid_size"] for c in calls} == {0.01, 0.02}
    assert summary["label"] == "IS-only"
    assert summary["n_cells"] == 2
    assert "holdout" not in summary
    assert "stitched_oos_funding" not in summary
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_research/test_walk_forward.py::test_oneshot_keys_keeps_reset_true_only tests/test_research/test_walk_forward.py::test_main_oneshot_skips_windows -v`
Expected: FAIL with `ImportError` for `oneshot_keys` / oneshot이 창 분할로 들어감

- [ ] **Step 3: Write minimal implementation**

`walk_forward.py`:

```python
def oneshot_keys(spec: GridSpec) -> list[tuple]:
    keys = param_keys(spec.grid)
    if "reset_enabled" not in spec.grid:
        return keys
    idx = sorted(spec.grid).index("reset_enabled")
    return [k for k in keys if k[idx] is True]


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def run_oneshot(settings, spec, wf_cfg, mc_cfg, data_start, data_end, days, out_dir) -> None:
    instrument_id = settings.instrument_id_str
    catalog_path = str(settings.catalog_path)
    cells = []
    for key in oneshot_keys(spec):
        params = _params_for(spec, key)
        res = run_window(
            catalog_path, instrument_id, settings=settings, spec=spec,
            params=params, start=data_start, end=data_end,
            warmup_days=wf_cfg.warmup_days,
        )
        cells.append({
            "params": params,
            "n_trades": res.n_trades,
            "total_pnl": res.total_pnl,
            "metrics": run_metrics(res, mc_cfg.initial_capital),
            "benchmark": bh_metrics(days, data_start, data_end),
        })
    strategy_name = spec.strategy_path.rsplit(":", 1)[-1]
    wf_report = {
        "label": "IS-only",
        "instrument_id": instrument_id,
        "data_start": data_start.isoformat(),
        "data_end": data_end.isoformat(),
        "cells": cells,
    }
    mc_report = {"oos": None, "holdout": None, "reason": DGT_MC_REASON}
    summary = {
        "label": "IS-only",
        "strategy": strategy_name,
        "instrument_id": instrument_id,
        "n_cells": len(cells),
        "cells": cells,
        "benchmark": bh_metrics(days, data_start, data_end),
        "assumptions": DGT_ASSUMPTIONS,
    }
    report.write_reports(out_dir, wf_report, mc_report, summary)
    report.print_summary(summary)
```

`main` 초반, `windows = compute_windows(...)` 전에:

```python
    strategy_name = spec.strategy_path.rsplit(":", 1)[-1]
    out_dir = report.run_dir(strategy_name)
    days = load_daily_closes()
    if _env_flag("WF_ONESHOT"):
        run_oneshot(settings, spec, wf_cfg, mc_cfg, data_start, data_end, days, out_dir)
        return
```

`strategy_name` / `out_dir` / `days`를 기존 `main`과 중복하지 않게, oneshot 분기를 기존 할당 직후에 둔다. 현재 `main`은 `detect_data_range` → `compute_windows` → `load_daily_closes` → `run_dir` 순이다. oneshot은 `compute_windows`를 건너뛰어야 하므로 `detect_data_range` 직후 분기한다:

```python
    data_start, data_end = detect_data_range(catalog_path, bar_type)
    if _env_flag("WF_ONESHOT"):
        days = load_daily_closes()
        out_dir = report.run_dir(spec.strategy_path.rsplit(":", 1)[-1])
        run_oneshot(settings, spec, wf_cfg, mc_cfg, data_start, data_end, days, out_dir)
        return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_research/test_walk_forward.py tests/test_strategies/test_dynamic_grid.py tests/test_strategies/test_no_exchange_io.py tests/test_data/test_funding.py tests/test_research/test_executor.py -v`
Expected: PASS. 기존 `test_main_wires_metrics_into_reports`는 `WF_ONESHOT`을 설정하지 않으므로 창 분할 경로를 유지.

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/research/walk_forward.py tests/test_research/test_walk_forward.py
git commit -m "feat(research): add WF_ONESHOT IS-only DGT diagnostic"
```

---

## DGT 실행 레시피 (코드 변경 없음)

본평가:

```bash
WF_GRID_PATH=research/grids/dgt_btc.json WF_WARMUP_DAYS=1 uv run wf-okx
```

논문 구간 진단 (승격 금지, IS-only):

```bash
WF_GRID_PATH=research/grids/dgt_btc.json WF_WARMUP_DAYS=1 WF_ONESHOT=1 WF_DATA_START=2021-01-01T00:00:00+00:00 WF_DATA_END=2024-07-31T00:00:00+00:00 uv run wf-okx
```

결과는 `logs/DynamicGrid/<timestamp>/{wf_report,mc_report,summary}.json`. Wiki/lifecycle은 건드리지 않는다.

---

## Spec coverage (self-review)

| Spec | Task |
|---|---|
| §4.1 Config, 원금은 잔고, 시크릿/노드 금지 | Task 4 |
| §4.2 geometric levels, 양자화 0 스킵 | Task 1, 4 `_place_ladder` |
| §4.3 첫 봉 M/2 시장가, 최소수량 미만 미시작 | Task 3 `open_grid`, Task 4 `_try_start` |
| §4.4 경로 일관 래더, 부분체결 후 재계산, 매도≤grid, reduce_only | Task 2, 3 `working_orders`/`apply_sell`, Task 4 |
| §4.5 상한/하한 리셋, `reset_enabled=false` 종료, 백 유지 | Task 3, 4 (`close_all_positions`로 상한 청산 금지) |
| §4.6 하이픈 금지, 짧은 `order_id_tag` | Task 4 Config |
| §5 엔진 비용 = 기존 venue 모델 | 파일 없음 (`build_run_config`) |
| §5 펀딩 후처리 병기 | Task 6 |
| §6.1 24칸 JSON, equity Sharpe, 게이트 marks≥2, 전역 WF 기본값 유지 | Task 5 |
| §6.2 WF 파이프라인, 창 종료 시 백 미청산 | Task 4 default + 기존 `main` |
| §6.3 `WF_ONESHOT`, reset_enabled=true 12칸, IS-only | Task 7 |
| §6.4 MC null + 사유 | Task 6, 7 |
| §7 `strategy_factory` 미등록 | Task 4/5 파일 목록 |
| §8 단위 테스트 목록 | Task 1–3, 5 |
| §9 가정을 결과 파일에 기록 | Task 6, 7 `summary["assumptions"]` |
| §10 비범위 | Global Constraints |
| §11 수락 조건 | 위 전 항목 |

Placeholders: 없음. `working_orders`의 `down` 순서만 Task 3 Step 4에서 테스트가 고정한다.

타입: `GridBook.open_grid(m, price, min_qty) -> bool`, `RunResult.fills`, `GridSpec.select`, `oneshot_keys`가 이후 태스크와 이름이 같다.
