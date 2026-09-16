# S01 Kalman Spread Model Specification

- 상태: DONE
- 범위: 1단계 모델 정의, 순수 계산 모듈, 테스트
- 제외: Strategy, BacktestNode, TradingNode, 주문·리스크·이벤트 게이트

## Document Links

- [Work index](../../../ideas/kalman_MR_spread/INDEX.md)
- [Implementation plan](../plans/2026-09-15-kalman-mr-s01-model.md)
- [Strategy source directory](../../../src/sngw_trader/strategies/)
- [Indicator source directory](../../../src/sngw_trader/indicators/)
- [Indicator tests](../../../tests/test_indicators/)

## 목표

현재 스펙의 원시 로그가격 칼만 필터는 `alpha`와 `beta`에 같은 상태 잡음 분산을 적용한다. 로그가격의 절대 수준이 커질수록 beta 잡음이 innovation 분산을 과도하게 키울 수 있고, 초기 상태와 공분산도 정의되어 있지 않다.

1단계에서는 모델을 가격 단위와 계수 단위가 섞이지 않도록 정규화하고, 초기화·warm-up·수치 안정성 규칙을 순수 Python 모듈로 고정한다.

## 모델

원시 가격은 양수일 때만 로그 변환한다.

```text
y_t = ln(PY_t)
x_t = ln(PX_t)
```

형성창에서 다음 두 값을 한 번 계산하고 거래창 동안 고정한다.

```text
x_center = mean(x_t)
x_scale = max(population_std(x_t), 1e-6)
u_t = (x_t - x_center) / x_scale
```

내부 상태는 다음과 같다.

```text
theta_t = [alpha_t, beta_scaled_t]'
y_t = alpha_t + beta_scaled_t * u_t + v_t
beta_t = beta_scaled_t / x_scale
```

`alpha`와 `beta_scaled`는 모두 로그 Y 가격 단위가 되므로 상태 잡음 공분산을 같은 스케일에서 정의할 수 있다. 헤지 수량에 사용하는 외부 beta는 항상 `beta_scaled / x_scale`로 복원한다.

상태 전이는 random walk다.

```text
theta_t = theta_(t-1) + w_t
F = I
Q = q * I
q = R * delta / (1 - delta)
```

관측식은 다음과 같다.

```text
H_t = [1, u_t]
S_t = H_t P_(t|t-1) H_t' + R
e_t = y_t - H_t theta_(t|t-1)
z_t = e_t / sqrt(S_t)
```

`z_t`는 상태 업데이트 전의 one-step innovation으로 계산한다.

## 초기화

모델은 첫 번째 유효한 관측에서 lazy initialization 한다.

```text
initial_beta = 1.0
beta_scaled_0 = initial_beta * x_scale
alpha_0 = y_0 - beta_scaled_0 * u_0
P_0 = diag(R, R)
```

초기 beta는 거래 판단용 가정이 아니라 warm-up 시작점이다. 모델은 beta를 클립하지 않는다. beta가 0 이하이거나 헤지 수량으로 사용할 수 없는 경우는 이후 formation/sizing 게이트에서 거래를 거부한다.

## 업데이트 및 warm-up

각 유효한 완성 봉마다 다음 순서로 업데이트한다.

1. `theta_pred = theta_prev` and `P_pred = P_prev + Q`
2. `y_pred = H @ theta_pred`
3. `S = H @ P_pred @ H.T + R`
4. `e = y - y_pred`
5. `K = P_pred @ H.T / S`
6. `theta = theta_pred + K * e`
7. Joseph form으로 `P`를 갱신한다.

```text
P = (I - K H) P_pred (I - K H)' + K R K'
```

갱신 후 `P`는 대칭화한다. `S`가 유한하고 양수인지 확인한다.

필터는 첫 봉부터 갱신하지만 첫 72봉은 신호로 사용하지 않는다. 72번째 유효 업데이트부터 `ready=True`로 보고 `z`를 거래 신호에 사용할 수 있다. 결측·비양수·비유한 가격은 업데이트하지 않고 호출자에게 오류로 알린다.

## 모듈 경계

추가 파일:

- `src/sngw_trader/indicators/kalman_spread.py`
- `tests/test_indicators/test_kalman_spread.py`

모듈은 다음만 담당한다.

- 양의 가격 검증과 로그 변환
- `x_center`, `x_scale`을 받은 정규화
- 2상태 칼만 예측·업데이트
- innovation, `S`, `z`, beta, warm-up 상태 반환

모듈은 다음을 담당하지 않는다.

- Nautilus import
- OKX/API/파일 I/O
- formation window 통계 계산
- 포지션 수량·계약 메타데이터 변환
- 진입·청산·리스크 판단

## 테스트 기준

테스트는 외부 프레임워크 의존 없이 기존 pytest 구조를 따른다.

- 잘못된 `R`, `delta`, `x_scale`, 가격을 거부한다.
- 첫 관측의 lazy initialization 결과가 결정적이다.
- `Q`가 `R * delta / (1 - delta)`로 스케일된다.
- innovation과 `z`가 posterior 업데이트 전에 계산된다.
- 알려진 선형 관계에서 beta가 올바른 방향으로 이동한다.
- 매 업데이트 후 `S > 0`, `P`가 대칭이고 음의 고유값을 만들지 않는다.
- 71번째 업데이트는 신호 미준비이고 72번째부터 준비 상태다.
- 모듈에 `nautilus_trader` import가 없다.

## 후속 단계와의 계약

다음 단계의 formation/sizing 구현은 반드시 다음 값을 사용한다.

- formation에서 계산한 `x_center`, `x_scale`
- 모델이 반환한 복원 beta
- `beta <= 0` 또는 수량 반올림 후 최소 계약 미달 시 거래 거부
- 거래창 동안 `x_center`, `x_scale`을 재계산하지 않음

비용, 계약 수량, 포트폴리오 노출, 실행 실패 처리는 이 단계에서 구현하지 않는다.

## Stage Handoff

- Previous stage: none
- Next stage: [S02 Cost Model](2026-09-15-kalman-mr-s02-cost.md)
- Completion gate: the S01 tests pass and the model contract is accepted by the S01 review.
