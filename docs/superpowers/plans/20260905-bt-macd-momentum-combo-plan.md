# MACD + Momentum Combo 백테스트 구현 계획

- Spec: `docs/superpowers/specs/20260905-bt-macd-momentum-combo-spec.md` (확정)
- 검증 관문 순서 고정: 단위 테스트 → IS → OOS → 리포트

## T1. indicators: rsi.py, mfi.py

- `Rsi(window=14)`: Wilder smoothing, `update(close) -> float | None`
- `Mfi(window=14)`: typical price money flow, `update(high, low, close, volume) -> float | None`
- 테스트: `tests/test_indicators/test_rsi.py`, `test_mfi.py` — 합성 시퀀스 기대값 (전부 상승 → RSI 100, MFI 100 등)

## T2. strategies/macd_momentum_combo.py

- `MacdMomentumComboConfig`: instrument_id, bar_type, trade_size, `momentum: "rsi"|"mfi"`, `allow_short=False`, `use_bracket=False`, macd 12/26/9, rsi 14 (35/70), mfi 14 (25/70), all_in: bool = True, bracket ATR 14 ×3
- 패턴: `macd_crossover.py` 그대로 — BarAggregator(86_400, volume) → `_decide` pure core → OrderIntent → `_execute`, `_signed_qty` fill 추적
- `_decide`: buy_cond = macd>signal AND all-of-6 momentum ≤ lower; sell_cond = macd<signal AND all-of-6 ≥ upper; target = buy→+1, sell→(−1 if allow_short else 0), else hold
- bracket: `use_bracket=True`면 DailyAtr/stop_price/bracket_hit (kd_macd_crypto 선례)
- all-in qty: `_execute`에서 포트폴리오 계좌 기반, qty step 내림
- 테스트: `tests/test_strategies/test_macd_momentum_combo.py` — all-of-6 판정, 방향 분기(allow_short), unit 모드 진입/청산, bracket 시딩

## T3. research/macd_momentum_combo_compare.py

- `macd_crossover_compare.py` 패턴, executor.run_window
- 행렬: {rsi, mfi} × {long_only, long_short} × {all_in, unit} × {base, stress} 16셀 + TP/SL sensitivity 2셀 × {IS, OOS}
- IS 2021-01-01~2022-12-31, OOS 2023-01-01~2025-12-30, warmup 버퍼
- JSON 저장 `logs/macd_momentum_combo/`, 백그라운드 실행

## T4. 실행·검증

1. `pytest tests/test_indicators/test_rsi.py tests/test_indicators/test_mfi.py tests/test_strategies/test_macd_momentum_combo.py`
2. IS 스모크 → 전체 행렬 백그라운드 실행
3. 신호 trace 확인 (trade count 0 셀 원인 구분)
4. OOS 실행

## T5. 리포트

- result: `LLM_WIKI/30_PROJECTS/trading/20260905-bt-macd-momentum-combo-result.md`
- receipt: `LLM_WIKI/40_AGENT_WORKSPACE/agents/trading-agent/20260905-bt-macd-momentum-combo-receipt.md` (accepted)
- verified vs assumption 구분, 스코프 격리 준수
