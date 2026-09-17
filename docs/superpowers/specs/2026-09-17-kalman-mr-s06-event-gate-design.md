# S06 Event Gate Design

- 상태: `DESIGN_REVIEW`
- 범위: 타입화된 이벤트 입력 계약, fail-closed 게이트 평가, 수동 Kill Switch 라우팅
- 제외: 뉴스 공급자 구현, 외부 API 호출, Nautilus 전략 연결(어댑터는 이후 스테이지)
- 이전 단계: [S05 Execution and Recovery Design](2026-09-17-kalman-mr-s05-execution-recovery-design.md)

## Module Boundary

Add:

- `src/sngw_trader/indicators/event_gate.py`
- `tests/test_indicators/test_event_gate.py`

The module is pure: frozen dataclasses and one deterministic evaluation
function. It does not import Nautilus, HTTP clients, or exchange SDKs, and
performs no I/O. The caller (actor, catalog, or manual control input) owns
event collection and delivery; the strategy adapter consumes the returned
decision and translates `exit_action` into the S05 transition API
(`begin_exit` for `ORDERLY_EXIT`, `emergency_flatten` for
`EMERGENCY_FLATTEN`).

## Event Model

`EventType` (minimal set, extensible later):

- `NEWS` — news impact on a symbol
- `HALT` — trading halt on a symbol or the venue
- `MANUAL_KILL` — manual kill switch

`EventRecord` (frozen dataclass):

- `event_id: str` — non-empty identity
- `event_type: EventType`
- `symbol: str | None` — `None` means global scope; otherwise symbol-specific
- `effective_at: float` — UTC epoch seconds
- `expires_at: float | None` — `None` means no expiry
- `source_status: str` — `ACTIVE` or `DISPUTED`
- `observed_at: float` — when the caller received the record (freshness)

Invalid records (non-finite timestamps, negative windows, empty id, unknown
status) raise `ValueError` at construction — the same fail-closed boundary as
S05.

`GateLimits` (frozen dataclass):

- `max_event_age_seconds: float` — positive; staleness threshold

## Evaluation Contract

```python
def evaluate_gate(
    events: Sequence[EventRecord],
    symbol: str,
    now: float,
    last_event_check: float,
    limits: GateLimits,
) -> GateDecision
```

`last_event_check` is the caller's event-feed poll timestamp; the module
owns no event cache, so freshness is an explicit input.

`GateDecision` (frozen dataclass):

- `entries_allowed: bool`
- `reasons: tuple[str, ...]`
- `exit_action: str` — `NONE`, `ORDERLY_EXIT`, or `EMERGENCY_FLATTEN`

Deterministic: same inputs → same decision. Timestamps are UTC epoch floats.

### Entry rules (fail-closed)

- No records at all → blocked with `MISSING_EVENT_STATE`.
- `now - last_event_check > max_event_age_seconds` → blocked with
  `STALE_EVENT_STATE`. `MISSING_EVENT_STATE` and `STALE_EVENT_STATE` are not
  exclusive — both are reported when both apply.
- A `DISPUTED` relevant record → blocked with `SOURCE_DISPUTED`; a disputed
  record never triggers an exit (untrusted source).
- A relevant record in effect (`effective_at <= now` and not expired) with
  symbol scope matching `symbol`, or global scope → blocked with
  `EVENT_ACTIVE_{TYPE}`.
- Out-of-window records (`now < effective_at`, expired) are ignored.
- Multiple matching events accumulate reasons.

### Exit routing (open positions)

- Active `MANUAL_KILL` (symbol-specific or global) → `EMERGENCY_FLATTEN`.
- Active `NEWS` or `HALT` → `ORDERLY_EXIT`.
- Both present → `EMERGENCY_FLATTEN` wins (priority), all reasons listed.
- Exit actions are directives only; the adapter applies them when a position
  is open. A flat position ignores the exit action.
- Stale or missing event state blocks entries but emits no exit action —
  absence of event data is not, by itself, an instruction to flatten.

## Error and Safety Rules

- Missing or stale event state fails closed for new entries (spec).
- Malformed records fail at construction with `ValueError`.
- Disputed sources block entries but never route exits.
- Determinism: no wall-clock reads, no randomness, no hidden state.
- The module never reads wall-clock time; `now` is always an argument.

## Test Contract

Focused tests cover:

- active, expired, and not-yet-effective events
- symbol-specific vs global scope matching
- missing and stale event state (fail-closed)
- disputed source (entry block, no exit)
- MANUAL_KILL → EMERGENCY_FLATTEN, NEWS/HALT → ORDERLY_EXIT, priority
- multiple events accumulate reasons
- deterministic repeat evaluation
- invalid record construction (ValueError)
- module purity (stdlib-only imports)

## Implementation Handoff

- Previous stage: [S05 Execution and Recovery](2026-09-17-kalman-mr-s05-execution-recovery-design.md)
- Existing stage specification: [S06](2026-09-15-kalman-mr-s06-event-gate.md)
- Next stage: [S07 Validation](2026-09-15-kalman-mr-s07-validation.md)
