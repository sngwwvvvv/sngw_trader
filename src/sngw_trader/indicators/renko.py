"""Close-only Renko brick builder. No Nautilus imports.

Standard convention (assumption — spec §1, §4.1):
- Up brick confirmed when price >= last_brick_close + B (multiple bricks allowed
  for a large move: the move is consumed B at a time).
- Direction flip down requires price <= last_brick_close - 2B (a 1B adverse
  move alone never flips).
- After a down brick, down continuation needs price <= last - B.
- The first price only seeds the state (no brick) — the first brick confirms
  on the first B move in either direction from the seed.
"""

from __future__ import annotations


class RenkoBrickBuilder:
    """Feed 1m closes; get confirmed brick closes back."""

    def __init__(self, brick_size: float) -> None:
        if brick_size <= 0:
            raise ValueError("brick_size must be > 0")
        self._b = float(brick_size)
        self._last: float | None = None  # last confirmed brick close
        self._up: bool = True  # current direction (irrelevant before first brick)

    @property
    def last_close(self) -> float | None:
        return self._last

    def on_close(self, price: float, brick_size: float | None = None) -> list[float]:
        """Consume one 1m close; return brick closes confirmed by it.

        brick_size overrides the constructor size for this call (relative
        bricks: callers pass price * pct).
        """
        b = float(brick_size) if brick_size is not None else self._b
        if b <= 0:
            raise ValueError("brick_size must be > 0")
        bricks: list[float] = []
        if self._last is None:
            self._last = price
            return bricks
        # ponytail: loop runs per-bar, brick count per move is small
        while True:
            last = self._last
            if self._up and price >= last + b:
                self._last = last + b
            elif not self._up and price <= last - b:
                self._last = last - b
            elif self._up and price <= last - 2 * b:
                self._last = last - b
                self._up = False
            elif not self._up and price >= last + 2 * b:
                self._last = last + b
                self._up = True
            else:
                break
            bricks.append(self._last)
        return bricks
