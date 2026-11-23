import pandas as pd
import pytest

from vic.data.generate import AS_OF_DEFAULT, generate_all


@pytest.fixture(scope="module")
def data() -> dict[str, pd.DataFrame]:
    return generate_all(seed=42)


def test_same_seed_gives_identical_data(data):
    again = generate_all(seed=42)
    for name, frame in data.items():
        pd.testing.assert_frame_equal(frame, again[name])


def test_different_seed_gives_different_trades(data):
    other = generate_all(seed=7)
    assert not data["trades"].equals(other["trades"])


def test_ids_are_unique(data):
    assert data["clients"]["client_id"].is_unique
    assert data["trades"]["trade_id"].is_unique
    assert data["positions"]["position_id"].is_unique


def test_every_client_has_existing_rep(data):
    assert (
        data["clients"]["sales_rep_id"].isin(data["sales_reps"]["sales_rep_id"]).all()
    )


def test_trades_reference_existing_clients_and_products(data):
    trades = data["trades"]
    assert trades["client_id"].isin(data["clients"]["client_id"]).all()
    assert trades["product_id"].isin(data["products"]["product_id"]).all()


def test_no_trade_after_as_of(data):
    assert (data["trades"]["trade_date"] <= pd.Timestamp(AS_OF_DEFAULT)).all()


def test_positions_mature_after_start_and_after_as_of(data):
    positions = data["positions"]
    assert (positions["maturity_date"] > positions["start_date"]).all()
    assert (positions["maturity_date"] > pd.Timestamp(AS_OF_DEFAULT)).all()


def test_aum_is_positive(data):
    assert (data["clients"]["aum_eur"] > 0).all()


def test_corporates_hedge_much_more_than_cooperative_banks(data):
    """The planted signal: corporates buy hedging products far more often."""
    trades = data["trades"].merge(data["clients"][["client_id", "segment"]])
    trades = trades.merge(data["products"][["product_id", "is_hedging_product"]])
    hedge_share = trades.groupby("segment")["is_hedging_product"].mean()
    assert hedge_share["corporate"] > 5 * hedge_share["cooperative_bank"]


def test_some_clients_are_inactive_for_90_days(data):
    last_trade = data["trades"].groupby("client_id")["trade_date"].max()
    cutoff = pd.Timestamp(AS_OF_DEFAULT) - pd.Timedelta(days=90)
    assert (last_trade < cutoff).sum() > 0


def test_some_positions_mature_within_30_days(data):
    horizon = pd.Timestamp(AS_OF_DEFAULT) + pd.Timedelta(days=30)
    assert (data["positions"]["maturity_date"] <= horizon).sum() > 0
