# S01 Kalman Spread Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a pure, numerically stable two-state Kalman spread filter with explicit scaling, initialization, and 72-bar warm-up.

**Architecture:** Keep the filter in `indicators/` with no Nautilus or exchange imports. The caller supplies formation-derived `x_center` and `x_scale`; the filter returns innovation statistics and the restored hedge beta.

**Tech Stack:** Python 3.12+, standard library, pytest.

**Spec:** [S01 Kalman Spread Model Specification](../specs/2026-09-15-kalman-mr-s01-model.md)

## Global Constraints

- The module must not import `nautilus_trader`.
- The module must not perform exchange, network, file, or event-loop I/O.
- `x_center` and `x_scale` are fixed for the formation/trading window.
- The filter updates during warm-up but does not expose a tradable signal before update 72.
- Beta is not clipped inside the filter; invalid beta is rejected by later sizing gates.

---

### Task 1: Define the public filter contract

**Files:**
- Create: `src/sngw_trader/indicators/kalman_spread.py`
- Test: `tests/test_indicators/test_kalman_spread.py`

**Interfaces:**
- Consumes: positive `y_price`, positive `x_price`, fixed `x_center`, and fixed positive `x_scale`.
- Produces: one update result containing innovation, innovation variance, z-score, restored beta, and warm-up state.

- [ ] Write tests for invalid configuration, non-positive prices, and non-finite values.
- [ ] Run `pytest tests/test_indicators/test_kalman_spread.py -q` and confirm the new tests fail because the module is absent.
- [ ] Add frozen configuration and update-result dataclasses with explicit validation.
- [ ] Add the filter constructor and `update(y_price, x_price)` method without any Nautilus import.
- [ ] Run `pytest tests/test_indicators/test_kalman_spread.py -q` and confirm the contract tests pass.

### Task 2: Implement the scaled Kalman recursion

**Files:**
- Modify: `src/sngw_trader/indicators/kalman_spread.py`
- Test: `tests/test_indicators/test_kalman_spread.py`

**Interfaces:**
- Consumes: the configuration and update contract from Task 1.
- Produces: `u=(ln(x_price)-x_center)/x_scale`, `beta=beta_scaled/x_scale`, and `z=e/sqrt(S)`.

- [ ] Add a deterministic hand-calculation test for the first innovation.
- [ ] Add a test asserting `q = R * delta / (1 - delta)` and positive innovation variance.
- [ ] Add a test that a known positive linear relation moves beta in the positive direction.
- [ ] Implement the prediction, innovation, gain, and posterior state update using scalar 2x2 math.
- [ ] Implement the Joseph covariance update and explicit covariance symmetrization.
- [ ] Run `pytest tests/test_indicators/test_kalman_spread.py -q` and confirm all recursion tests pass.

### Task 3: Add warm-up and numerical invariants

**Files:**
- Modify: `src/sngw_trader/indicators/kalman_spread.py`
- Test: `tests/test_indicators/test_kalman_spread.py`

**Interfaces:**
- Consumes: the filter state from Task 2.
- Produces: `ready=False` for updates 1-71 and `ready=True` from update 72 onward.

- [ ] Add boundary tests for updates 71 and 72.
- [ ] Add a repeated-update test asserting finite values, `S > 0`, symmetric covariance, and non-negative 2x2 covariance determinant within tolerance.
- [ ] Implement the ready counter without suppressing filter updates.
- [ ] Add a source-level test that the module contains no `nautilus_trader` import.
- [ ] Run `pytest tests/test_indicators/test_kalman_spread.py tests/test_indicators/test_risk_metrics.py -q`.

### Task 4: Update the idea documents

**Files:**
- Modify: `ideas/kalman_MR_spread/OKX_Kalman_MR_Spec.md`
- Modify: `ideas/kalman_MR_spread/OKX_Kalman_MR_Rules.xlsx`
- Modify: `ideas/kalman_MR_spread/INDEX.md`

**Interfaces:**
- Consumes: the tested filter contract from Tasks 1-3.
- Produces: matching model definitions in the Markdown and spreadsheet operational assumptions.

- [ ] Replace the raw `Q = delta/(1-delta) * I` wording with the scaled state definition.
- [ ] Add fixed formation `x_center` and population `x_scale` rules.
- [ ] Add the lazy initialization, Joseph update, and warm-up boundary.
- [ ] Mark S01 `DONE` only after the implementation and focused tests pass.
- [ ] Run the focused tests and inspect the document links in `INDEX.md`.
