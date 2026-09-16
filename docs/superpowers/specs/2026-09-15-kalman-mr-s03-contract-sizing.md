# S03 Contract Sizing Specification

- 상태: `DONE`
- 범위: restored beta를 OKX 계약 수량과 실제 노셔널로 변환
- 제외: 포트폴리오 한도와 주문 제출

## Document Links

- [Work index](../../../ideas/kalman_MR_spread/INDEX.md)
- [Previous: S02](2026-09-15-kalman-mr-s02-cost.md)
- [Plan](../plans/2026-09-15-kalman-mr-s03-contract-sizing.md)
- [Next: S04](2026-09-15-kalman-mr-s04-portfolio-risk.md)

## Required Decisions

- Quantity conversion uses `ctVal`, `lotSz`, `minSz`, and price precision from the instrument metadata.
- The sizing layer rejects non-positive beta and quantities below the minimum contract size.
- Rounding is followed by a second notional calculation; the result, not the pre-rounding target, is passed to risk checks.
- Y notional is `N`; X notional is `abs(beta) * N` only when beta passes the positive-beta gate.

## Acceptance Criteria

- BTC, ETH, and a small-contract example convert to valid contract quantities.
- Rounding never silently produces a zero leg for an accepted trade.
- Actual gross notional and hedge error are returned for later portfolio checks.
- Invalid instrument metadata fails explicitly.

## Implemented Contract

- `src/sngw_trader/indicators/contract_sizing.py` contains pure `Decimal`
  arithmetic and no Nautilus, exchange, or order imports.
- `ContractSpec` accepts `ct_val`, `lot_sz`, `min_sz`, and
  `price_precision`. `contract_spec_from_instrument` maps those values from a
  Nautilus-like linear USDT instrument at the integration boundary and rejects
  inverse or non-USDT instruments.
- `size_pair` rejects non-positive target notional, prices, beta, and contract
  metadata. Prices are rounded down to the instrument precision, then raw
  contract quantities are rounded down to `lot_sz`.
- A rounded price of zero, or a quantity below `min_sz` or equal to zero,
  raises `ValueError`; it is never silently accepted.
- `PairSizing` returns both rounded quantities, recalculated leg notionals,
  gross notional, and absolute hedge error. Risk checks must consume these
  recalculated values rather than pre-rounding targets.

## Stage Handoff

- Previous: [S02](2026-09-15-kalman-mr-s02-cost.md)
- Next: [S04](2026-09-15-kalman-mr-s04-portfolio-risk.md)
