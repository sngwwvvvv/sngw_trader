# 변동성 타깃 사이징 엔진 설계 (설계 확정)

- 날짜: 2026-09-01
- 근거: Harvey et al., “The Impact of Volatility Targeting”, JPM 2018
- 선행: `docs/superpowers/specs/2026-08-30-err-momentum-design.md`, `docs/superpowers/specs/2026-08-31-4h-ermom-trailing-design.md`
- 상태: 설계 확정 (사용자 승인: 순수 계산 모듈 + 밴드 리밸런스, `vol_filter` 삭제)

## 1. 목적

방향을 정하는 전략과 **얼마나 들고 있을지를 정하는 엔진**을 분리한다.

- 전략: 롱 / 숏 / 플랫만 결정한다. \(\sigma\) 공식을 갖지 않는다.
- 엔진: 방향과 진입 타이밍을 모른다. 목표 수량과 “지금 손댈지”만 반환한다.
- `vol_filter`(고볼 시 신규 진입 차단)는 끄지 않고 **삭제**한다. 고볼은 진입 거부가 아니라 **작은 수량**이다.

엔진이 약속하는 것은 샤프 개선이 아니다. 같은 신호에서 실현 위험을 더 고르게 하고, 왼쪽 꼬리를 줄이는 것이다. BTC에서는 \(1/\sigma\) 스케일이 모멘텀과 같은 방향으로 움직인다. 그 오염은 4칸 비교로 측정하고, 알파로 주장하지 않는다.

## 2. 확정된 계약

| 항목 | 결정 |
|---|---|
| 형태 | Nautilus Actor/러너가 아닌 **순수 계산 모듈** (`indicators/`) |
| 주문 | 엔진은 `submit_order`를 하지 않는다. 전략만 주문한다 |
| 추정 | 일봉 수익률 EWMA 분산, 평균 0 (제곱 수익률). 롤링 표본표준편차 `RealizedVol`을 쓰지 않는다 |
| 시차 | 저장소 룩어헤드 규칙. 마감 봉에서 갱신한 scale은 그 봉에서 낸 주문의 **다음 봉 시가** 체결에만 쓰인다. 엔진 안에 \(t-2\)를 추가하지 않는다 |
| 사후 \(k\) | 없음. 라이브·백테스트 모두 \(k=1\) |
| 단위 | `trade_size` = \(\hat\sigma = \sigma_{\text{target}}\)일 때의 계약 수. NAV/가격으로 예산을 다시 계산하지 않는다 |
| 리밸런스 | **밴드**. 같은 방향에서 \(\lvert q^{\ast}/q - 1\rvert > b\)일 때만 델타. 청산·플립·신규 진입은 밴드 무시 |
| 밴드 기본 | \(b = 0.10\) |
| 변동성 입력 | 두 전략 모두 **UTC 일봉 종가**. Spec A의 4h ERMOM과 독립 |
| `vol_filter` | 필드·env·분기·테스트 전부 삭제. 호환 레이어 없음 |
| 연구 스위치 | `sizing_mode`: `vol_target` \| `fixed`. `fixed`는 scale=1 (옛 고정 명목). 필터가 아니다 |

1차에서 빼는 것: Actor, NAV 예산, 포트폴리오 2단 스케일, 분봉 realized vol, 사후 \(k\), ATR 스톱 변경, ERMOM/리본 변경.

## 3. 파일 구성

```
src/sngw_trader/
  indicators/
    vol_targeting.py           # 신규: VolTargetSizer (Nautilus import 금지)
    risk_metrics.py            # 수정: RealizedVol 삭제 (전략 미사용이면)
  strategies/
    err_momentum_regime.py     # 수정: 필터 삭제, sizer, 델타 주문, UTC 일봉 집계
    err_mom_ema30_entry.py     # 수정: 필터 삭제, sizer, 델타 주문
  runners/
    strategy_factory.py        # 수정: vol_filter_* 제거, sizing 필드 주입. 매매 조건 없음
  config/
    settings.py                # 수정: vol_filter_* 삭제, sizing 필드 추가
.env.example                   # 수정: VOL_* 삭제, SIZE_* / SIZING_MODE 추가
tests/test_indicators/
  test_vol_targeting.py        # 신규
  test_risk_metrics.py         # 수정: RealizedVol 테스트 삭제
tests/test_strategies/         # 수정: 필터 테스트 삭제, 고볼=소수량 진입, 밴드 리밸런스
tests/test_config/             # 수정: Settings 필드
tests/test_runners/            # 수정: factory / backtest settings 픽스처
```

러너 조립(`backtest_okx.py`, `live_okx.py`)은 전략 부착 경로가 그대로이면 로직 변경 없음. 팩토리가 새 config를 넘긴다.

## 4. 엔진 — `indicators/vol_targeting.py`

Nautilus `Strategy` / `Actor` / 노드를 import하지 않는다. `risk_metrics.RealizedVol`을 호출하지 않는다.

### 4.1 Config

```python
@dataclass(frozen=True)
class VolTargetConfig:
    target_vol: float          # 연율, > 0. 기본 0.20
    half_life: int             # 일, >= 1. 기본 20
    min_scale: float           # >= 0. 기본 0.0
    max_scale: float           # > min_scale. 기본 3.0
    rebalance_band: float      # >= 0. 기본 0.10
    periods_per_year: int      # 기본 365. 일봉 전용
    mode: str                  # "vol_target" | "fixed"
```

생성 시 `mode`가 두 값 중 하나가 아니면 ValueError. `target_vol <= 0`, `half_life < 1`, `min_scale < 0`, `max_scale <= min_scale`, `rebalance_band < 0`, `periods_per_year < 1`이면 ValueError.

### 4.2 추정

일봉 단순수익률 \(r_t = P_t/P_{t-1} - 1\).

\[
\lambda = 2^{-1/H}
\]

첫 수익률에서 \(v_1 = r_1^2\). 이후

\[
v_t = \lambda v_{t-1} + (1-\lambda)\, r_t^2
\]

평균을 빼지 않는다. 짧은 창에서 평균 수익을 추정하지 않으려는 Harvey 선택이다.

\[
\hat\sigma_t = \sqrt{v_t \cdot \texttt{periods\_per\_year}}
\]

전략이 일봉만 넣으므로 `periods_per_year`는 365로 고정한다. Settings 필드가 아니다.

`mode == "fixed"`이면 \(\hat\sigma\) 를 계산하지 않아도 되고, scale은 항상 1이다.

`mode == "vol_target"`일 때 scale은 수익률이 `3 * half_life`개 쌓인 뒤에만 유효하다. 반감기 20일이면 60개. 그 전 `scale()` / 비제로 `desired_qty`는 `None`.

유효할 때

\[
s_t = \mathrm{clip}\bigl(\sigma_{\text{target}} / \hat\sigma_t,\; s_{\min},\; s_{\max}\bigr)
\]

\(\hat\sigma_t = 0\)이면 scale은 `max_scale`이다 (0으로 나누지 않음).

사후 보정 \(k\) 없음.

### 4.3 공개 메서드

```text
update(close: float) -> float | None
    마감된 UTC 일봉 종가만. 연율 σ 또는 (워밍업 / fixed) 시 None.
    fixed 모드에서도 호출은 허용하되 scale에 쓰지 않는다.

scale() -> float | None
    vol_target: 워밍업 전이면 None, 이후 clip된 s_t.
    fixed: 항상 1.0 (워밍업 없음).

desired_qty(direction: int, unit_qty: Decimal) -> Decimal | None
    direction ∈ {-1, 0, +1}. 그 외 ValueError.
    direction == 0 → 항상 0 (워밍업과 무관. 청산에 σ가 필요 없다).
    direction != 0, fixed → direction * unit_qty.
    direction != 0, vol_target, 워밍업 → None.
    그 외 → direction * s_t * unit_qty.

should_rebalance(current_qty: Decimal, desired_qty: Decimal) -> bool
    둘 다 부호 있는 수량. None을 받지 않는다.
```

`should_rebalance` 규칙 (순서 고정):

1. `current == 0` 이고 `desired == 0` → False
2. `current == 0` 이고 `desired != 0` → True (신규 진입, 밴드 무시)
3. `desired == 0` 이고 `current != 0` → True (청산, 밴드 무시)
4. `current`와 `desired`의 부호가 다르면 → True (플립, 밴드 무시)
5. 같은 부호: \(\lvert desired / current - 1 \rvert > b\) 이면 True, 아니면 False. `b == 0`이면 조금이라도 다르면 True

엔진은 양자화하지 않는다. `make_qty`는 전략이 한다.

### 4.4 엔진이 하지 않는 일

- 주문, 포지션 조회, 캐시 조회
- 롱/숏 결정, 쿨다운, 스톱
- “볼이 높으니 진입 금지”
- 워밍업 중 고정 명목으로 폴백

## 5. 전략이 엔진을 쓰는 방법

사이징 파라미터는 StrategyConfig 필드다. 전략 `__init__`이 `VolTargetConfig` / `VolTargetSizer`를 만든다. 팩토리는 필드만 복사하고 `vol_targeting`을 import하지 않는다.

두 전략 모두 사이저를 들고, **UTC 일봉 종가**로만 `update`한다.

- Spec B: 이미 `BarAggregator(86_400)`가 있다. `RealizedVol.update` 자리를 `sizer.update`로 바꾼다.
- Spec A: ERMOM은 4h(`BarAggregator(14_400)`) 유지. 사이저 전용으로 `BarAggregator(86_400)`를 추가한다. 4h 수익률로 \(\sigma\)를 재지 않는다.

일봉과 신호 봉이 같은 1분봉에서 끝나면 순서 고정:

1. 일봉 완성 → `sizer.update(close)`
2. 전략 방향 로직 (Spec A: 4h 레짐, Spec B: 30분 FSM/청산)
3. 방향이 바뀌면(진입·청산·플립) **밴드 무시**하고 목표 수량으로 주문
4. 방향이 그대로이고 이번 봉에서 일봉이 완성됐고 포지션이 있으면 `should_rebalance` 후 델타

같은 봉에서 3이 주문을 냈으면 4는 건너뛴다 (방금 맞춘 수량을 다시 손대지 않음).

### 5.1 목표 수량 → 주문

`current_signed`: 롱 `+qty`, 숏 `-qty`, 플랫 `0`. Portfolio/Cache에서 읽는다. 전략이 별도 포지션 dict를 두지 않는다.

`desired = sizer.desired_qty(direction, trade_size)`

| 상황 | 행동 |
|---|---|
| `desired is None` 이고 플랫 | 신규 진입 없음 (워밍업). 고정 명목 폴백 금지 |
| `desired is None` 이고 보유 중 | 유지. 리밸런스 없음 |
| `direction == 0` 또는 `desired == 0` 이고 보유 중 | 전량 청산, 스톱 리셋 |
| 플랫 → 비제로 `desired` | `abs(desired)` 시장가 진입, 스톱 시드 (기존 fill 경로) |
| 부호 반대 (플립) | 전량 청산 + 스톱 리셋 후 `abs(desired)` 반대 진입 |
| 같은 부호, 밴드 밖 | **델타만** 시장가. 스톱·워터마크·쿨다운·FSM을 리셋하지 않음 |
| 같은 부호, 밴드 안 | 주문 없음 |

델타: `delta = desired_signed - current_signed`. `delta > 0`이면 BUY, `< 0`이면 SELL, 수량 `abs(delta)`. `instrument.make_qty` 후 0이면 주문 생략.

같은 방향 리밸런스는 `close_all_positions` 후 재진입이 아니다. 스톱이 풀리고 왕복 수수료가 나가기 때문이다.

### 5.2 Spec A (`err_momentum_regime.py`)

- `vol_filter_enabled` / `vol_lookback` / `vol_threshold` / `_vol` 삭제
- `_target_4h`의 `entry_blocked`는 **쿨다운만**. `apply_entry_block` 시맨틱 유지 (신규만 차단, 보유는 유지)
- 레짐이 유지되는 한 always-in: 사이저가 `desired == 0`으로 줄여 플랫이 된 뒤 \(\sigma\)가 내려오면, 다음 4h에서 레짐이 여전히 비제로면 다시 진입한다. 별도 FSM 없음
- 1분 트레일링 스톱은 그대로. 델타 리밸런스는 워터마크를 건드리지 않음

### 5.3 Spec B (`err_mom_ema30_entry.py`)

- vol 필터 분기 삭제. `blocked`는 `regime == 0` (및 EMA 미준비)만
- **사이저가 신규 포지션을 만들지 않는다.** 신규 진입은 기존처럼 TRIG에서만. 그때 수량이 `desired_qty`
- 보유 중 일봉 밴드 리밸런스는 `L_IN` / `S_IN`을 유지한 채 델타만
- `desired == 0`으로 사이징 청산하면 FSM을 다른 청산과 같이 리셋한다. 볼이 내려와도 TRIG를 다시 통과하기 전에는 재진입하지 않음

### 5.4 ATR 스톱

변경 없음. 엔진은 수량, 스톱은 청산 시점이다.

## 6. `vol_filter` 삭제

남기지 않는다. 기본값 `false` 플래그도 없다.

삭제 대상:

- `Settings.vol_filter_enabled`, `vol_lookback`, `vol_threshold`
- 두 StrategyConfig의 동일 필드
- `strategy_factory` 전달
- env `VOL_FILTER_ENABLED`, `VOL_LOOKBACK`, `VOL_THRESHOLD`
- Spec A `apply_entry_block`으로 들어가던 vol 분기, Spec B `blocked`의 vol 항
- `test_vol_block_still_applies_without_cooldown` 및 동등 테스트
- WF 그리드의 `vol_filter_enabled` 축 (그리드 JSON/문서에서 제거)

`RealizedVol`은 두 전략에서 빠진다. 다른 호출자가 없으면 클래스와 테스트도 삭제한다. ATR/`Ema`/스톱 헬퍼는 유지.

행동 변화 (회귀 테스트의 핵심):

- 이전: \(\sigma > 0.80\)이면 신규 진입 없음. 기존 포지션은 **풀 사이즈** 유지
- 이후: 고볼이어도 신호만 있으면 진입한다. 수량이 작다. 기존 포지션도 밴드 밖이면 줄어든다

## 7. 설정

`trade_size` 의미 변경: 항상 그 계약 수가 아니라, **타깃 볼일 때의 단위 수량**.

| 필드 | env | 기본 | 제약 |
|---|---|---|---|
| `trade_size` | `TRADE_SIZE` | `"0.01"` | 기존과 동일 문자열 Decimal |
| `sizing_mode` | `SIZING_MODE` | `vol_target` | `vol_target` \| `fixed`. 그 외 SystemExit |
| `size_target_vol` | `SIZE_TARGET_VOL` | `0.20` | > 0 |
| `size_half_life` | `SIZE_HALF_LIFE` | `20` | >= 1 |
| `size_min_scale` | `SIZE_MIN_SCALE` | `0.0` | >= 0 |
| `size_max_scale` | `SIZE_MAX_SCALE` | `3.0` | > min |
| `size_rebalance_band` | `SIZE_REBALANCE_BAND` | `0.10` | >= 0 |

`fixed` 모드에서는 target/half_life/clip/band가 scale에 영향을 주지 않는다. 필드는 StrategyConfig에 그대로 두고, 전략이 `mode="fixed"`인 `VolTargetConfig`를 만든다.

BTC 실현볼이 흔히 50–80%이면 타깃 20%의 평균 스케일은 대략 0.25–0.4배다. 타깃은 평균 레버리지 나사다. 1차에서 타깃을 최적화하지 않는다.

## 8. 검증

채택 기준은 ERMOM이 B&H를 이겼다가 아니다. 구현 수락과 연구 해석을 나눈다.

### 8.1 구현 수락 (이 스펙의 완료 조건)

엔진 유닛 (`tests/test_indicators/test_vol_targeting.py`):

- 첫 수익률 \(v_1 = r^2\), 이후 EWMA가 손계산과 일치
- `scale == clip(target / σ)`
- \(\sigma = 0\)이면 `max_scale`, ZeroDivision 없음
- `desired_qty` 부호 = direction, 0이면 0
- vol_target 워밍업 동안 비제로 direction은 `None`
- fixed는 워밍업 없이 scale 1
- 밴드 안 False, 진입/청산/플립 True, `band == 0`이면 같은 부호 불일치 True
- Nautilus import 없음 (`test_no_exchange_io` 패턴과 동일하게 모듈 스캔 가능하면 포함)

전략:

- 고볼 + 롱 신호 → 주문이 **나가고** 수량은 `trade_size`보다 작다 (필터 회귀 방지)
- 워밍업 미완료 + vol_target + 플랫 → 주문 없음
- `sizing_mode=fixed` → 수량이 `trade_size` (기존과 동일, 필터만 없는 상태)
- 같은 방향, 밴드 안 → 추가 주문 없음
- 같은 방향, 밴드 밖 → 델타만, 스톱 가격 유지 (Spec A)
- Spec B: 일봉 리밸런스가 FSM TRIG 없이 신규 진입을 만들지 않음

설정/팩토리: `vol_filter_*` 없음. sizing 필드가 두 전략 config에 주입됨.

기존 테스트의 `Settings(...)` / StrategyConfig 픽스처에서 vol 필드를 빼고 sizing 필드를 넣는다.

### 8.2 연구 해석 (구현 후, 이 스펙의 코딩 완료와 별개)

네 칸. 동일 기간·동일 비용.

| | `fixed` | `vol_target` |
|---|---|---|
| 항상 롱 | A | B (논문 재현에 가장 가까움) |
| ERMOM 방향 | C | D |

- B−A: 엔진이 BTC 롱에 하는 일 (꼬리, vol-of-vol, 비용)
- D−C: 같은 엔진이 ERMOM 위에 하는 일
- D−C ≈ B−A 이면 사이징이 신호와 거의 독립
- D−C ≪ B−A 이면 ERMOM이 이미 그 모멘텀을 먹은 것

샤프 상승을 새 알파로 읽지 않는다. 이 비교 하니스는 1차 코딩 범위가 아니다. `sizing_mode`가 그리드 축이 될 수 있게 필드만 열어 둔다.

## 9. 비범위 / 금지

- 새 러너, 자체 루프, `ccxt` / `python-okx` 주문
- 전략 파일의 TradingNode / OKX factory import
- 러너에 \(\sigma\) 공식이나 밴드 규칙
- `vol_filter` 별칭 유지
- 전체표본 실현볼을 타깃에 맞추는 \(k\)
- 사이저가 `submit_order` 하거나 포지션 dict를 소유
