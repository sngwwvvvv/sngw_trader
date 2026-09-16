# OKX OI 수집기 스펙 (Railway 배포, 별도 repo)

- 작성: 2026-09-17
- 배경: OKX rubik open-interest-history API는 약 4~5일만 소급 가능(2026-09-17 프로브).
  실측 OKX OI를 축적하려면 지금부터 전진 수집이 필요하다.
- 이 문서는 별도 repo에서 구현한다. tripletail repo에 구현하지 않는다.

## 1. 목적

OKX BTC-USDT-SWAP 5분 Open Interest를 매일 수집하여 Railway Volume에
월별 parquet으로 축적한다. 축적 데이터는 tripletail 백테스트 카탈로그에
pull-sync 되어 OI 전략의 실측 재검증 구간을 만든다.

## 2. 비목표

- 가격 bar 수집 없음 (OKX 1m bar는 tripletail의 catalog_writer로 소급 가능)
- 타 심볼/타 거래소 수집 없음
- 주문/트레이딩 기능 없음. `ccxt`, `python-okx` 사용 금지 — 원본 REST만
- 실시간 WebSocket 스트리밍 없음 (REST 히스토리 폴링만)

## 3. 데이터 원천

- Endpoint: `GET https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history`
- Params: `instId=BTC-USDT-SWAP&period=5m&end=<cursor_ms>&limit=100`
- 응답 rows: `[ts_ms, oi, oiCcy, oiCcyQuote]`. **저장 필드는 `ts`와 `oi`만**
  (tripletail `OpenInterestPoint`와 동일한 contract-value 필드)
- 페이지네이션: 가장 오래된 row의 ts를 다음 `end`로 쓰며 소급. 48시간 커버에
  필요한 만큼 반복 (약 576 rows, 6페이지)
- Rate limit: 페이지당 0.5초 sleep

## 4. 수집 동작 (Railway Cron, 매일 03:00 UTC)

1. `end = now_ms + 300_000` 커서로 소급 fetch 시작
2. 최근 **48시간** 구간의 rows 수집 (API 깊이 4~5일 > 48h이므로 overlap 충분.
   cron 실패 1회는 다음날 실행이 덮는다. 2회 연속 실패까지도 5일 깊이가 커버)
3. `ts` 기준 5분 정렬 검증, `ts` 중복 dedupe (key = ts_ms)
4. 기존 월 parquet과 병합: 같은 ts가 있으면 기존 값 유지(불변 원칙), 새 ts만 append
5. `/data/oi/BTC-USDT-SWAP/YYYY-MM.parquet` 에 write (파티션 = 월)
   - 스키마: `ts_ms: int64, open_interest: float64`
   - write는 임시 파일 생성 후 atomic rename
6. 결과 로그: 수집 rows 수, 신규 append 수, 스킵(중복) 수
7. **Gap 감지**: 신규 append가 기대치(기존 마지막 ts ~ 현재 사이 5분 경계 수)의
   90% 미만이면 stderr 경고 로그 + exit code 2 (Railway 대시보드에서 실패로 보임)

## 5. 멱등성

- 같은 날 재실행해도 결과 동일: 중복 ts는 스킵, 값 변경 없음
- 월 경계 처리: 48h 커버가 두 달에 걸치면 두 파티션에 각각 append

## 6. 저장소 (Railway Volume)

- Volume mount: `/data`
- 레이아웃:
  ```
  /data/oi/BTC-USDT-SWAP/2026-09.parquet
  /data/oi/BTC-USDT-SWAP/2026-10.parquet
  ```
- 크기 추정: 5m OI = 하루 288 rows ≈ 연간 ~420KB parquet. 10GB volume이면
  사실상 무제한

## 7. 백업 / pull-sync (volume 유실 대비)

- Railway Volume은 서비스 삭제 시 데이터 소실. **월 1회 이상** 로컬로 pull:
  ```
  railway ssh --service oi-collector -- "cat /data/oi/BTC-USDT-SWAP/2026-09.parquet" > 2026-09.parquet
  ```
  (파일별로 반복. 파일 목록은 로그 또는 `ls`로 확인)
- pull-sync 받은 parquet은 tripletail repo의 카탈로그에 병합하는 변환기가
  필요하면 그때 tripletail에 추가한다 (지금 만들지 않음)

## 8. 구현 요구사항 (별도 repo)

- 언어: Python 3.12. 의존성은 `pyarrow`만 (HTTP는 stdlib urllib)
- 파일 구성:
  ```
  main.py          # collect() 1회 실행 — Railway cron이 이것을 호출
  requirements.txt # pyarrow
  .env.example     # 변수 없음(공개 API). 필요시 OI_SYMBOL만
  README.md        # 이 스펙 요약 + Railway 배포 절차
  ```
- Railway 설정: Service type = Cron (매일 03:00 UTC), Volume mount `/data`
- 배포 절차는 README에 5줄 이내로: repo 연결 → Cron 설정 → Volume attach → 최초
  수동 실행 → 로그 확인

## 9. 검수 기준

- [ ] 2회 연속 실행해도 parquet row 수가 중복 없이 동일하게 유지된다
- [ ] 48h 이전 데이터(API 깊이 밖) 요청 시 경고 없이 정상 종료된다
- [ ] 의도적으로 ts를 하나 빼고 재실행하면 해당 ts만 append된다
- [ ] gap 감지 경고가 exit code 2로 이어진다
