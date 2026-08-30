# OKX 공개 REST 기반 카탈로그 라이터 설계

- 날짜: 2026-08-30
- 상태: 초안 (설계 협의 완료, spec 승인 대기)
- 적용 규칙: `NAUTILUS_VIBE_RULES.md` (다운로드 소스는 writer에만, 전략은 BarType만 인지)

## 1. 목적

백테스트 러너(`backtest_okx.py`)가 읽는 `ParquetDataCatalog`(기본 `./catalog`)에
OKX 공개 REST API로 1분봉 바 데이터를 적재하는 라이터를 만든다.

현재 `src/sngw_trader/data/catalog_writer.py`는 placeholder(`write_placeholder_note`)
만 있는 상태다. 이를 실제 다운로더로 교체한다.

## 2. 확정된 요구사항 (협의 결과)

| 항목 | 결정 |
|---|---|
| 데이터 소스 | OKX 공개 REST API (`/api/v5/market/history-candles`) |
| 데이터 타입 | 1분봉 `Bar`만 (tick 없음) |
| BarType | `{instrument_id}-1-MINUTE-LAST-EXTERNAL` (러너 `default_bar_type`과 일치) |
| 심볼 | `OKX_SYMBOL` env (기본 `BTC-USDT-SWAP`) |
| 범위 | `CATALOG_START` / `CATALOG_END` **필수값** (비면 에러) |
| 인스트루먼트 | `OKXInstrumentProvider`로 실제 인스트루먼트 로드 후 catalog에 적재 |
| 페이지네이션 | `end` → `start` 역방향, `after` 커서 + `limit=100` |
| 코드 위치 | `src/sngw_trader/data/catalog_writer.py` |
| 진입점 | `pyproject.toml` console script `catalog-download` |

## 3. 아키텍처

```
data/catalog_writer.py
  - download_bars(settings)  : 페이지네이션 + Bar 변환 + catalog 적재
  - okx_instrument(...)      : OKXInstrumentProvider로 인스트루먼트 로드
  - raw_candle_to_bar(...)   : 원시 캔들 → Bar 변환 (순수 함수)
  - main()                   : CLI 진입점
```

역할 분리 원칙:

- HTTP 클라이언트는 `get_cached_okx_http_client()`에서 얻는다 (키 불필요, 공개 시세 전용).
- 인스트루먼트 로드는 `OKXInstrumentProvider(client, instrument_types=[SWAP])` 사용.
- catalog 적재는 `ParquetDataCatalog.write_data([...], data_cls=Bar|Instrument)` 사용.
- 전략/러너 로직은 없다. 주문 관련 코드는 전혀 없다.

## 4. 다운로드 흐름

1. `get_cached_okx_http_client()`로 클라이언트 생성.
2. `OKXInstrumentProvider`로 대상 심볼 인스트루먼트 1개 로드 → `catalog.write_data([inst], data_cls=Instrument)`.
3. `/market/history-candles?instId={symbol}&bar=1m&after={older_ts}&limit=100` 를
   `end`에서 `start`까지 역방향 페이지네이션.
   - `after`는 "이 타임스탬프보다 과거" 필터. 각 응답은 최신 → 과거 순.
   - 응답의 가장 오래된 ts를 다음 요청의 `after`로 사용해 이어받는다.
4. 원시 캔들 `[ts, o, h, l, c, vol, ...]` → `Bar` 변환.
5. `catalog.write_data(bars, data_cls=Bar)`.
6. 완료 시 적재 개수/기간 프린트.

### 4.1 Bar 변환 규칙

- `ts`: ms epoch → `datetime` (UTC, Nautilus가 기대하는 형식).
- o/h/l/c: 캔들 문자열 → `Decimal`.
- vol: base volume.
- `BarType = {instrument_id}-1-MINUTE-LAST-EXTERNAL`.
- `BarSpecification`은 `BarType.from_str(...)`로 파싱해 재사용.

### 4.2 종료 조건

- `start` 시점에 도달하거나, API가 더 과거 데이터 없음 응답을 주면 종료.
- 요청 사이 최소 간격 유지 (OKX rate limit 준수, `X-RateLimit` 헤더/표준 제한).

## 5. 설정

`settings.py`에 추가:

| env | 타입 | 필수/기본 | 설명 |
|---|---|---|---|
| `CATALOG_START` | `str` (ISO8601/YYYY-MM-DD) | 필수 | 다운로드 시작(과거) |
| `CATALOG_END` | `str` (ISO8601/YYYY-MM-DD) | 필수 | 다운로드 종료(최근) |

- 둘 중 하나라도 비면 `SystemExit` 에러로 종료.
- `.env.example`에 두 변수 추가 (값 없음).

## 6. 진입점

`pyproject.toml` 추가:

```
catalog-download = "sngw_trader.data.catalog_writer:main"
```

## 7. 테스트

| 대상 | 수준 |
|---|---|
| `raw_candle_to_bar` | 순수 단위 테스트: 원시 캔들 리스트 → Bar 리스트 검증 |
| 범위 파싱/검증 | `CATALOG_START/END` 파싱 + 필수값 에러 |
| 러너와의 BarType 일치 | `catalog_writer`의 BarType == `backtest_okx.default_bar_type` |

네트워크 필수 테스트는 기본 스위트에 넣지 않는다. 실제 다운로드는 수동 실행으로 검증.

## 8. UNVERIFIED IMPORT (구현 시 확인 필수)

- `get_cached_okx_http_client` — `nautilus_trader.adapters.okx.factories` (확인됨 1.231.0).
- `OKXInstrumentProvider` — `nautilus_trader.adapters.okx` (확인됨 1.231.0).
- `ParquetDataCatalog.write_data(data, data_cls=...)` — `nautilus_trader.persistence.catalog` (확인됨 1.231.0).
- `Bar` / `BarType` 생성자 필드 — `Bar.from_dict` / `BarType.from_str` (확인됨 1.231.0, 생성자 인자는 builtin이라 구현 시 확인).
- `/market/history-candles` 응답 캔들 배열 필드 순서와 `after` 커서 시맨틱 — 공식 문서 기준, 구현 시 실데이터로 확인.

import 실패 시 임의 클래스를 만들지 않고 설치된 패키지에서 확인한다 (규칙 §5.3).

## 9. 범위 밖 (YAGNI)

- 틱 데이터 (`QuoteTick`/`TradeTick`)
- Tardis 소스 (지금은 OKX REST만, 소스 교체 인터페이스는 함수 단위로만 분리)
- 점진적 이어받기(incremental update) — 재실행 시 해당 범위 다시 받음
- 여러 심볼 동시 다운로드
- 인증 필요한 프라이빗 데이터