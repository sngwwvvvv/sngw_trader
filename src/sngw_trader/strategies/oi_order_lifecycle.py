"""Shared order-failure handling for the OI strategies."""


def request_flatten(strategy, *, force: bool = False) -> bool:
    if strategy._force_flat_close_pending and not force:
        return False
    strategy.close_all_positions(strategy.config.instrument_id)
    return True


def handle_order_failure(strategy, event) -> None:
    instrument_id = getattr(event, "instrument_id", strategy.config.instrument_id)
    if instrument_id != strategy.config.instrument_id:
        return

    order_id = getattr(event, "client_order_id", None)
    if strategy._same_order_id(order_id, strategy._entry_order_id):
        strategy._pending_setup = None
        strategy._captured_atr = None
        strategy._captured_target = None
        if strategy._signed_qty != 0:
            strategy.cancel_all_orders(strategy.config.instrument_id)
            request_flatten(strategy)
            strategy._clear_trade_state()
        else:
            strategy._clear_entry_state()
        return

    failed_order = None
    sibling = None
    if strategy._same_order_id(order_id, strategy._stop_order_id):
        failed_order = "stop"
        sibling = strategy._target_order
    elif strategy._same_order_id(order_id, strategy._target_order_id):
        failed_order = "target"
        sibling = strategy._stop_order
    if failed_order is None or strategy._protective_failure_handled:
        return

    strategy._protective_failure_handled = True
    if failed_order == "stop":
        strategy._stop_order = None
        strategy._stop_order_id = None
    else:
        strategy._target_order = None
        strategy._target_order_id = None
    if sibling is not None:
        strategy.cancel_order(sibling)
    request_flatten(strategy)
