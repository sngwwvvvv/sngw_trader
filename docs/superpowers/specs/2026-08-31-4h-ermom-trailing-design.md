# 4h 중단기 ERMOM + 트레일링 스톱 전환 설계

- 날짜: 2026-08-31
- 대상: `src/sngw_trader/strategies/err_momentum_regime.py` (기존 스펙 A, 설계: `2026-08-30-err-momentum-design.md`)
- 상태: 설계 확정 (사용자 승인)

## 1. 목적

기존 스펙 A는 일봉 × 200일 ERMOM 리짐 팔로워로, 신호가 하루 1회 갱신되고 방향 전환이 대추세 종료 후에야 발생해 암호화 시장에서 엣지가 없다. 동일한 ERMOM 코어를 **4h 봉 + 단축 윈도우 + 트레일링 스톱**으로 전환해 목표 홀딩 2~7일의 중단기 전략으로 바꾼다.

## 2. 결정 사항

| 항목 | 결정 |
|---|---|
| 집계 주기 | 일봉(86,400초) → **4h봉(14,400초)** — `BarAggregator(14_400)`, 소스는 기존 1분봉 그대로 |
| ERMOM 파라미터 | `w_f=5, w_e=5, momentum_window=48` (약 8일). 워밍업 58×4h ≈ **10일** (기존 220일) |
| 방향 | 롱/숏 유지 (`regime_target` 그대로: `>θ` 롱, `<−θ` 숏, 그 외 플랫) |
| 진입/청산 룰 | 진입은 레짐 룰 그대로. **청산을 고정 ATR 스톱 → 샹들리에 트레일링 스톱으로 교체** (반응성 확보의 핵심) |
| 스톱 기준 | 4h ATR(14) — `DailyAtr` 재사용, 주기만 4h로 |
| 스톱 히트 후 | 다음 4h 봉에 레짐 룰로 재진입 (기존 동작, 주기만 단축) |
| vol 킬스위치 | 4h 실현변동성 기준으로 전환 (`RealizedVol` 재사용). 진입 차단/기존 포지션 유지 동작 unchanged |
| 킬스위치(외부) | `apply_entry_block` 그대로 유지 |
| 사이징/집행 | 명목 1 단위, 봉 마감 시장가 → 다음 봉 시가 체결 (기존과 동일, 룩어헤드 없음) |
| 러너 | **수정 없음** — 러너는 1분 소스봉 구독, 집계는 전략 내부이므로 `strategy_factory`/`backtest_okx` 무변경 |
| 1차 제외 | 브레이크아웃 확인 필터, 멀티 TF 필터, 포지션 사이징 스케일링 — 4h 결과 보고 추가 판단 |

## 3. 파일 구성

```
src/sngw_trader/
  strategies/
    err_momentum_regime.py     # 수정: 집계 주기, 기본 파라미터, 트레일링 스톱
  indicators/
    risk_metrics.py            # 사실상 무변경 (주만 정리). stop_price/is_stop_hit/DailyAtr 재사용
    err_momentum.py            # 무변경 — ErrorAdjustedMomentum는 주기 무관 스트리밍
```

### 3.1 `strategies/err_momentum_regime.py`

**Config 변경**

- `w_f: int = 5`, `w_e: int = 5`, `momentum_window: int = 48`
- `theta`, `atr_period=14`, `atr_mult=3.0`, vol 관련 필드는 기존 유지 (백테스트 캘리브레이션 대상)

**on_bar 흐름 (기존 구조 유지)**

1. 1분봉 → `BarAggregator(14_400)`로 4h봉 합성
2. 4h봉 완성 시: `ermom.update` → `atr.update` → `vol.update` → `_on_bar_4h`
3. `_on_bar_4h`:
   - 포지션 보유 중이면 **트레일링 스톱 갱신 후** 히트 검사:
     - 롱: `stop = max(stop, high_water − atr_mult × ATR)` (high_water = 진입 이후 4h 고점 최댓값)
     - 숏: `stop = min(stop, low_water + atr_mult × ATR)` (low_water = 4h 저점 최솟값)
     - `is_stop_hit(close)` → 전량 청산, 리셋. 재진입은 다음 4h 봉의 레짐 룰
   - 레짐 타깃 계산 (`regime_target` + `apply_entry_block` 킬스위치) → 기존과 동일하게 플립/진출입

**트레일링 상태**

- `_stop_price` 외에 `_high_water: float | None` / `_low_water: float | None` 추가
- 진입 체결(`OrderFilled`) 시 진입가로 워터마크 시드 + `stop_price()`로 초기 스톱 (기존 `_pending_atr` 흐름 재사용)
- 봉의 high/low로 워터마크 갱신 → 스톱 갱신. **스톱은 단조 갱신만** (롱에서 내려가지 않음, 숏에서 올라가지 않음)
- 트레일링 계산은 전략 내 한 줄 (`max`/`min`) — 순수 함수 신규 추가 없음

### 3.2 유지되는 것 (무변경)

- `apply_entry_block` 시맨틱: 킬스위치는 신규 진입만 차단, 플립 시 플랫으로
- `on_stop` 시 `close_all_positions`
- 펀딩비 후처리, 리포트 파이프라인 (`research/`)

## 4. 파라미터 캘리브레이션

- `research/param_scan.py`로 4h 그리드 스캔: `momentum_window ∈ {24, 48, 60}`, `atr_mult ∈ {2.5, 3.0, 4.0}`, `theta ∈ {0, 0.1}`
- 비교 기준: 기존 일봉 스펙 A 결과 vs 4h 전환 — net 수익(비용 반영), Sharpe, MaxDD, 거래 횟수, 평균 홀딩
- 봉 주기 단축으로 거래 횟수 증가 → 수수료/슬리피지가 지배하지 않는지 net 기준으로 확인
- vol_threshold(연율화 기준값)는 4h 변동성 분포에 맞게 스캔 결과로 재설정

## 5. 테스트

1. **트레일링 스톱 시나리오 테스트**: 진입 → 고점 갱신 → 스톱 상향(단조) → 되돌림 → 스톱 히트 → 청산 → 다음 봉 재진입. 숏 방향 대칭 케이스 포함
2. **기존 테스트 이관**: 레짐→주문 전이, 킬스위치 블록, 룩어헤드 없음 테스트는 4h 파라미터로 재검증
3. **워밍업 단축 확인**: 58개 4h봉 후 ERMOM 유효값 발생

## 6. 알려진 근사 / 갭

- 4h 집계봉은 OKX 공식 4h 캔들과 소스는 같으나 경계/가격 기준이 미세하게 다를 수 있음 — 라이브 전환 시 동일 집계 로직이라 상대 비교에 영향 없음
- 트레일링 스톱도 봉 종가 기준 판정 → 다음 봉 시가 체결 (스톱 주문 예약 없음, 기존 정책 동일)
- `momentum_window=48` 등 기본값은 1차 스캔 출발점이며 최적값 아님 — walk-forward로 검증 후 확정
