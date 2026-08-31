# 4h 중단기 ERMOM + 트레일링 스톱 전환 설계

- 날짜: 2026-08-31
- 대상: `src/sngw_trader/strategies/err_momentum_regime.py` (기존 스펙 A, 설계: `2026-08-30-err-momentum-design.md`)
- 상태: 설계 수정 (2026-08-31 리뷰 반영: 1분봉 스톱 트랙, 비용 1차 관문, ablation, 쿨다운)

## 1. 목적

기존 스펙 A는 일봉 × 200일 ERMOM 리짐 팔로워로, 신호가 하루 1회 갱신되고 방향 전환이 대추세 종료 후에야 발생해 암호화 시장에서 엣지가 없다. 동일한 ERMOM 코어를 **4h 봉 + 단축 윈도우 + 트레일링 스톱**으로 전환해 목표 홀딩 2~7일의 중단기 전략으로 바꾼다.

단기 전환의 최대 리스크는 비용 민감도다. 검증 관문을 비용 스트레스 → ablation → 스캔/walk-forward 순으로 고정한다 (§4).

## 2. 결정 사항

| 항목 | 결정 |
|---|---|
| 집계 주기 | 일봉(86,400초) → **4h봉(14,400초)** — `BarAggregator(14_400)`, 소스는 기존 1분봉 그대로 |
| ERMOM 파라미터 | `w_f=5, w_e=5, momentum_window=48` (약 8일). 워밍업 58×4h ≈ **10일** (기존 220일) |
| 방향 | 롱/숏 유지 (`regime_target` 그대로: `>θ` 롱, `<−θ` 숏, 그 외 플랫) |
| 진입/청산 룰 | 진입은 레짐 룰 그대로. **청산을 고정 ATR 스톱 → 샹들리에 트레일링 스톱으로 교체** (ablation으로 정당화, §4) |
| 스톱 기준 | 4h ATR(14) — `DailyAtr` 재사용, 주기만 4h로 |
| **스톱 감지 해상도** | **1분봉 스트림. 매 1분봉마다 히트 검사 → 워터마크 갱신 → 스톱 재계산 순서 고정.** 러너가 이미 1분봉을 전달하므로 러너 무변경, 백테스트/라이브 동일 코드 경로 |
| 스톱 히트 후 | 즉시 청산 (시장가 → 다음 **1분**봉 시가 체결). 재진입은 쿨다운 경과 후 4h 레짐 룰 |
| **재진입 쿨다운** | `reentry_cooldown_bars`(4h 기준, 기본 1). 스톱 히트 직후 레짐이 유지돼도 즉시 재진입하지 않음 — 체어링 방지 |
| vol 킬스위치 | 4h 실현변동성 기준. `RealizedVol`에 **`periods_per_year=2190`, `vol_lookback=120`**(≈20일) 명시 — 일봉용 기본값(365, 20)을 그대로 쓰면 값/창이 모두 틀어짐 |
| 킬스위치(외부) | `apply_entry_block` 그대로 유지. 쿨다운·변동성 필터는 `entry_blocked` 입력으로만 전달 (시맨틱 무변경) |
| 사이징/집행 | 명목 1 단위, 봉 마감 시장가 → 다음 봉 시가 체결 (기존과 동일, 룩어헤드 없음) |
| 러너 | **수정 없음** — 러너는 1분 소스봉 구독, 집계는 전략 내부이므로 `strategy_factory`/`backtest_okx` 무변경 |
| 1차 제외 | 브레이크아웃 확인 필터, 멀티 TF 필터, 포지션 사이징 스케일링 — 4h 결과 보고 추가 판단 |

## 3. 파일 구성

```
src/sngw_trader/
  strategies/
    err_momentum_regime.py     # 수정: 4h 집계, 기본 파라미터, 1분 스톱 트랙(_trailing), 트레일링 스톱, 쿨다운(_target_4h/_tick_4h)
  indicators/
    risk_metrics.py            # 수정: RealizedVol(lookback, periods_per_year) 파라미터화 (로직 무변경). 기본값 ppy=2190/lookback=120
    err_momentum.py            # 무변경 — ErrorAdjustedMomentum는 주기 무관 스트리밍
  research/
    param_scan.py              # 수정: Sharpe/MaxDD/평균 홀딩 리포트, WARMUP_DAYS 축소, 비용 배수 주입 (§4)
```

### 3.1 `strategies/err_momentum_regime.py`

**Config 변경**

- `w_f: int = 5`, `w_e: int = 5`, `momentum_window: int = 48`
- `vol_lookback: int = 120`, `reentry_cooldown_bars: int = 1` (신규)
- `theta`, `atr_period=14`, `atr_mult=3.0`, vol 관련 필드는 백테스트 캘리브레이션 대상

**on_bar 흐름 — 1분 트랙과 4h 트랙 이원화**

러너가 주는 1분봉마다 `on_bar` 호출. 한 번의 `on_bar`에서:

1. **1분 트랙 (포지션 보유 중, 매 1분봉)** — 순서 고정:
   - ① **히트 검사 먼저**: 기존 스톱 vs 이번 1분봉 극값 (롱: `low < stop`, 숏: `high > stop`)
     - 히트 시: 전량 청산(시장가, 다음 1분봉 시가 체결), 스톱/워터마크/쿨다운 리셋. 이번 봉 처리 종료
   - ② **워터마크 갱신**: 롱 `_high_water = max(_high_water, high)`, 숏 `_low_water = min(_low_water, low)`
   - ③ **스톱 재계산**: 롱 `stop = _high_water − atr_mult × ATR`, 숏 `stop = _low_water + atr_mult × ATR` — **단조 갱신만** (롱에서 내려가지 않음, 숏에서 올라가지 않음)
   - ATR은 4h 종가에서만 갱신되므로 봉 내 고정 — ③은 1분마다 워터마크 변화만 반영
2. **4h 트랙 (4h봉 완성 시)**:
   - `ermom.update` → `atr.update` → `vol.update`, 쿨다운 카운터 감소
   - 레짐 타깃 계산 (`regime_target` + `apply_entry_block`에 `entry_blocked = 변동성 필터 OR 쿨다운 중 OR 킬스위치`) → 플립/진출입

**순서가 중요한 이유**: ①을 먼저 하지 않으면 "이번 봉 high로 스톱을 올린 뒤 같은 봉 low로 히트"하는 자기참조가 생김. 히트 검사를 항상 이전 상태의 스톱 기준으로 하면 보수적으로 해소되고, 잔여 오차는 1분 스케일.

**트레일링 상태**

- `_stop_price` 외에 `_high_water: float | None` / `_low_water: float | None`, `_cooldown_bars: int` 추가
- 진입 체결(`OrderFilled`) 시: **포지션이 0→±1로 전이된 경우에만** 진입가로 워터마크 시드 + `stop_price()`로 초기 스톱. 부분체결로 추가 `OrderFilled`이 와도 워터마크를 재시드하지 않음 (기존 `_pending_atr` 흐름 재사용 + 가드)
- 트레일링 계산은 전략 내 한 줄 (`max`/`min`) — 순수 함수 신규 추가 없음

### 3.2 유지되는 것 (무변경)

- `apply_entry_block` 시맨틱: 차단은 신규 진입만, 플립 시 플랫으로
- `on_stop` 시 `close_all_positions`
- 펀딩비 후처리, 리포트 파이프라인 (`research/`)

## 4. 검증 관문 (순서 고정)

**1차 관문 — 비용 스트레스 테스트.** 스탯이 좋아도 여기서 탈락하면 4h 전환 자체를 폐기한다.

- 수수료/슬리피지 1×(기본), **2×, 3×** 세 가지로 대표 콤보 재실행
- **구현 범위 없음**: 엔진 수정 아님. `build_run_config`가 `settings.bt_maker_fee`/`bt_taker_fee`를 그대로 소비하므로 `param_scan`에서 요율에 배수 k를 곱해 넘기는 것만으로 충분. `realized_pnl`은 수수료 차감 후라 net에 그대로 반영
- taker 요율 ×k는 bps 기준 슬리피지 포함 상한 프록시 (go/no-go 판정용). fill-price 후처리 보정은 필요 시 별도
- 2×~3×에서 net 기준 Sharpe/수익이 붕괴(예: Sharpe 음전환, 수익 소멸)하면 비용 지배로 판정 → 기각. 주기 단축 전략의 표준 관문이며, 통과 전에 어떤 파라미터 튜닝도 하지 않는다

**2차 관문 — ablation.** 변경 두 개(주기, 스톱)를 분리하지 않으면 원인 파악이 불가능하다.

- (a) 4h + 기존 고정 ATR 스톱
- (b) 4h + 트레일링 (본 스펙)
- (c) 일봉 스펙 A (기준)
- (b)가 (a)를 **net 기준**으로 이기지 못하면 트레일링을 제외하고 워터마크 로직 전체 삭제

**3차 — 그리드 스캔 + walk-forward.**

- 그리드: `momentum_window ∈ {24, 48, 60}`, `atr_mult ∈ {2.5, 3.0, 4.0}`, `theta ∈ {0, 0.1, 0.2}`, `reentry_cooldown_bars ∈ {1, 2}`
- 비교 지표: net 수익(비용 반영), Sharpe, MaxDD, 거래 횟수, 평균 홀딩
- walk-forward로 검증 후 확정 (기본값은 출발점이지 최적값 아님)

**스캔 인프라 수정 (`research/param_scan.py`)**

- Sharpe/MaxDD/평균 홀딩 리포트 추가 (현재 realized/total/round_trips/fees만 출력)
- `WARMUP_DAYS = 330 → 30` (4h 워밍업 10일 + 여유) — 미축소 시 스캔이 수십 배 느림
- 비용 배수: fee 요율 settings에 k 곱셈 (엔진 무변경, 위 1차 관문 참조)

## 5. 테스트

1. **트레일링 스톱 시나리오 테스트**: 진입 → 고점 갱신 → 스톱 상향(단조) → 되돌림 → 스톱 히트 → 청산 → 쿨다운 → 재진입. 숏 방향 대칭 케이스 포함
2. **1분봉 관통 테스트**: 4h low가 스톱 관통 후 4h close 회복 — 기존 close 기반 판정이 놓치는 케이스를 1분 트랙에서 청산함을 확인
3. **히트-검사-우선 순서 테스트**: 이번 봉 high가 스톱을 올리더라도 같은 봉의 히트 판정은 이전 스톱 기준으로 이뤄짐
4. **부분체결 워터마크 가드**: 0→±1 전이에서만 워터마크 시드, 추가 체결 이벤트가 워터마크/스톱을 클리어하지 않음
5. **쿨다운 테스트**: 스톱 히트 후 `reentry_cooldown_bars` 동안 재진입 차단, 해제 후 레짐 룰 재진입. 차단 중 플립은 플랫 유지
6. **기존 테스트 이관**: 레짐→주문 전이, 킬스위치 블록, 룩어헤드 없음 테스트는 4h 파라미터로 재검증
7. **워밍업 단축 확인**: 58개 4h봉 후 ERMOM 유효값 발생. `RealizedVol(ppb=2190, lookback=120)` 유효값 발생

## 6. 알려진 근사 / 갭

- 1분 트랙 내에서도 1분봉 high/low의 발생 순서는 미지 — 히트 검사를 워터마크 갱신보다 먼저 해 보수적으로 처리하며, 1분 스케일이라 영향 미미
- 스톱 체결은 시장가(다음 1분봉 시가) — 어불성 갭이 4h 기준 "최대 4시간"에서 "최대 1분"으로 축소. 예약 스톱 주문은 실거래 슬리피지 분석 후 필요 시 검토
- 4h 집계봉은 OKX 공식 4h 캔들과 소스는 같으나 경계/가격 기준이 미세하게 다를 수 있음 — 라이브/백테스트 동일 집계·동일 스톱 코드 경로라 상대 비교에 영향 없음
- `momentum_window=48` 등 기본값은 1차 스캔 출발점이며 최적값 아님 — walk-forward로 검증 후 확정
