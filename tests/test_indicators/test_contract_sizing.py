from decimal import Decimal

import pytest

from sngw_trader.indicators.contract_sizing import (
    ContractSpec,
    contract_spec_from_instrument,
    size_pair,
)


def test_btc_contracts_convert_target_notional():
    result = size_pair(
        target_notional_usdt=Decimal("1000"),
        beta=Decimal("1.5"),
        y_price=Decimal("50000"),
        x_price=Decimal("2500"),
        y_contract=ContractSpec(Decimal("0.01"), Decimal("1"), Decimal("1"), 0),
        x_contract=ContractSpec(Decimal("0.1"), Decimal("1"), Decimal("1"), 0),
    )

    assert result.y_quantity == Decimal("2")
    assert result.x_quantity == Decimal("6")
    assert result.y_notional_usdt == Decimal("1000.00")
    assert result.x_notional_usdt == Decimal("1500.0")
    assert result.gross_notional_usdt == Decimal("2500.00")
    assert result.hedge_error_usdt == Decimal("0.00")


def test_eth_and_small_contracts_round_down_and_recalculate_notionals():
    eth = size_pair(
        target_notional_usdt=Decimal("1000"),
        beta=Decimal("1.5"),
        y_price=Decimal("333.333"),
        x_price=Decimal("222.222"),
        y_contract=ContractSpec(Decimal("0.01"), Decimal("1"), Decimal("1"), 2),
        x_contract=ContractSpec(Decimal("0.1"), Decimal("1"), Decimal("1"), 2),
    )
    small = size_pair(
        target_notional_usdt=Decimal("1"),
        beta=Decimal("1"),
        y_price=Decimal("0.12345"),
        x_price=Decimal("0.23456"),
        y_contract=ContractSpec(Decimal("0.001"), Decimal("0.1"), Decimal("0.1"), 4),
        x_contract=ContractSpec(Decimal("0.001"), Decimal("0.1"), Decimal("0.1"), 4),
    )

    assert eth.y_quantity == Decimal("300")
    assert eth.x_quantity == Decimal("67")
    assert eth.y_notional_usdt == Decimal("999.9900")
    assert eth.x_notional_usdt == Decimal("1488.8740")
    assert eth.hedge_error_usdt == Decimal("11.1110")
    assert small.y_quantity == Decimal("8103.7")
    assert small.x_quantity == Decimal("4264.3")
    assert small.y_notional_usdt == Decimal("0.99999658")
    assert small.x_notional_usdt == Decimal("0.99997835")


def test_rejects_non_positive_beta_and_invalid_resulting_quantity():
    args = dict(
        target_notional_usdt=Decimal("100"),
        y_price=Decimal("100"),
        x_price=Decimal("100"),
        y_contract=ContractSpec(Decimal("1"), Decimal("1"), Decimal("1"), 0),
        x_contract=ContractSpec(Decimal("1"), Decimal("1"), Decimal("1"), 0),
    )
    with pytest.raises(ValueError, match="beta must be positive"):
        size_pair(beta=Decimal("0"), **args)
    small_args = {**args, "target_notional_usdt": Decimal("0.5")}
    with pytest.raises(ValueError, match="quantity below minimum"):
        size_pair(beta=Decimal("1"), **small_args)


def test_rejects_invalid_contract_metadata_and_prices():
    with pytest.raises(ValueError, match="ct_val must be positive"):
        ContractSpec(Decimal("0"), Decimal("1"), Decimal("1"), 0)
    with pytest.raises(ValueError, match="price_precision"):
        ContractSpec(Decimal("1"), Decimal("1"), Decimal("1"), -1)


def test_contract_spec_adapter_reads_nautilus_like_metadata():
    class Instrument:
        multiplier = Decimal("0.01")
        lot_size = Decimal("1")
        min_quantity = Decimal("1")
        price_precision = 2

    result = contract_spec_from_instrument(Instrument())

    assert result == ContractSpec(Decimal("0.01"), Decimal("1"), Decimal("1"), 2)


def test_contract_spec_adapter_rejects_inverse_and_non_usdt_instruments():
    class InverseInstrument:
        multiplier = Decimal("1")
        lot_size = Decimal("1")
        min_quantity = Decimal("1")
        price_precision = 2
        is_inverse = True
        quote_currency = "USD"
        settlement_currency = "USD"

    with pytest.raises(ValueError, match="linear USDT"):
        contract_spec_from_instrument(InverseInstrument())


def test_rejects_price_that_rounds_to_zero_at_exchange_precision():
    spec = ContractSpec(Decimal("1"), Decimal("1"), Decimal("1"), 2)

    with pytest.raises(ValueError, match="price must remain positive"):
        size_pair(
            target_notional_usdt=Decimal("1"),
            beta=Decimal("1"),
            y_price=Decimal("0.009"),
            x_price=Decimal("1"),
            y_contract=spec,
            x_contract=spec,
        )
