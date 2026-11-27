"""Feature view tests on a hand-made dataset.

as_of = 2026-06-30, so: as_of - 90 = 2026-04-01, as_of - 180 = 2026-01-01,
as_of + 30 = 2026-07-30, as_of - 12 months = 2025-06-30.
"""

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from vic.db import get_connection, load_all

AS_OF = date(2026, 6, 30)

PRODUCTS = (
    ("P001", "Bundesanleihe 10J", "government_bond", 10.0, 1, False),
    ("P003", "Pfandbrief 5J", "covered_bond", 5.0, 1, False),
    ("P008", "Zinsswap 5J", "rates_derivative", 5.0, 3, True),
    ("P011", "Devisentermingeschäft 1J", "fx_derivative", 1.0, 2, True),
    ("P012", "Termingeld 3M", "money_market", 0.25, 1, False),
    ("P013", "Termingeld 12M", "money_market", 1.0, 1, False),
    ("P015", "Renten-ETF", "fund", None, 3, False),
)

# C1 is active, C2 used to be active, C3 never traded.
TRADES = (
    ("T01", "2026-06-20", "C1", "P001", 1_000_000),
    ("T02", "2026-05-01", "C1", "P001", 2_000_000),
    ("T03", "2026-04-01", "C1", "P003", 500_000),  # exactly as_of - 90
    ("T04", "2026-02-10", "C1", "P015", 1_000_000),  # product without tenor
    ("T05", "2026-07-05", "C1", "P001", 9_000_000),  # after as_of, ignored
    ("T06", "2026-02-15", "C2", "P008", 4_000_000),
    ("T07", "2025-03-01", "C2", "P008", 1_000_000),
    ("T08", "2025-06-30", "C2", "P011", 1_000_000),  # exactly 12 months back
    ("T09", "2025-07-01", "C2", "P011", 1_000_000),
)

POSITIONS = (
    ("POS1", "C1", "P003", 3_000_000, "2021-07-30", "2026-07-30"),  # exactly +30
    ("POS2", "C1", "P001", 2_000_000, "2016-07-31", "2026-07-31"),  # +31
    ("POS3", "C2", "P008", 1_000_000, "2021-06-30", "2026-06-30"),  # matured today
    ("POS4", "C2", "P012", 5_000_000, "2026-04-01", "2026-07-01"),  # +1
    ("POS5", "C3", "P013", 1_000_000, "2026-07-10", "2026-07-20"),  # starts later
)


def view_data() -> dict[str, pd.DataFrame]:
    trades = pd.DataFrame(
        TRADES,
        columns=["trade_id", "trade_date", "client_id", "product_id", "notional_eur"],
    )
    trades["trade_date"] = pd.to_datetime(trades["trade_date"])
    positions = pd.DataFrame(
        POSITIONS,
        columns=[
            "position_id",
            "client_id",
            "product_id",
            "notional_eur",
            "start_date",
            "maturity_date",
        ],
    )
    positions["start_date"] = pd.to_datetime(positions["start_date"])
    positions["maturity_date"] = pd.to_datetime(positions["maturity_date"])
    return {
        "sales_reps": pd.DataFrame(
            {"sales_rep_id": ["R01"], "name": ["Anna Becker"], "region": ["Nord"]}
        ),
        "products": pd.DataFrame(
            PRODUCTS,
            columns=[
                "product_id",
                "name",
                "asset_class",
                "tenor_years",
                "risk_level",
                "is_hedging_product",
            ],
        ),
        "clients": pd.DataFrame(
            {
                "client_id": ["C1", "C2", "C3"],
                "name": [
                    "Volksbank Birnfeld",
                    "Energie Tannhagen",
                    "Pensionskasse Ulm",
                ],
                "segment": ["cooperative_bank", "corporate", "institutional"],
                "region": ["Nord", "Nord", "Nord"],
                "aum_eur": [800e6, 150e6, 5e9],
                "risk_profile": ["conservative", "balanced", "balanced"],
                "has_floating_rate_debt": [False, True, False],
                "sales_rep_id": ["R01", "R01", "R01"],
            }
        ),
        "trades": trades,
        "positions": positions,
    }


@pytest.fixture(scope="module")
def con():
    connection = get_connection(":memory:")
    load_all(connection, view_data(), AS_OF)
    yield connection
    connection.close()


def test_run_context_holds_as_of(con):
    assert con.execute("SELECT * FROM vic.run_context").fetchall() == [(AS_OF,)]


def test_client_product_last_trade(con):
    rows = con.execute(
        "SELECT * FROM vic.v_client_product_last_trade ORDER BY client_id, product_id"
    ).fetchall()
    assert rows == [
        ("C1", "P001", date(2026, 6, 20), 2, Decimal("3000000.00")),
        ("C1", "P003", date(2026, 4, 1), 1, Decimal("500000.00")),
        ("C1", "P015", date(2026, 2, 10), 1, Decimal("1000000.00")),
        ("C2", "P008", date(2026, 2, 15), 2, Decimal("5000000.00")),
        ("C2", "P011", date(2025, 7, 1), 2, Decimal("2000000.00")),
    ]


def test_client_activity(con):
    rows = con.execute(
        "SELECT * FROM vic.v_client_activity ORDER BY client_id"
    ).fetchall()
    assert rows == [
        # last 90d: T01 + T02; previous 90d: T03 (boundary) + T04
        ("C1", date(2026, 6, 20), 10, Decimal("3000000.00"), Decimal("1500000.00")),
        # no trade in the last 90 days, but active before
        ("C2", date(2026, 2, 15), 135, Decimal("0"), Decimal("4000000.00")),
        ("C3", None, None, Decimal("0"), Decimal("0")),
    ]


def test_upcoming_maturities(con):
    rows = con.execute(
        "SELECT * FROM vic.v_upcoming_maturities ORDER BY days_to_maturity"
    ).fetchall()
    assert rows == [
        ("POS4", "C2", "P012", date(2026, 7, 1), Decimal("5000000.00"), 1),
        ("POS1", "C1", "P003", date(2026, 7, 30), Decimal("3000000.00"), 30),
    ]


def test_client_asset_class_mix(con):
    rows = con.execute(
        """
        SELECT client_id, CAST(asset_class AS VARCHAR), notional_eur, share
        FROM vic.v_client_asset_class_mix
        ORDER BY client_id, asset_class
        """
    ).fetchall()
    # ORDER BY on an ENUM follows its declaration order in schema.sql.
    expected = [
        ("C1", "government_bond", Decimal("3000000.00"), 2 / 3),
        ("C1", "covered_bond", Decimal("500000.00"), 1 / 9),
        ("C1", "fund", Decimal("1000000.00"), 2 / 9),
        # T07 and T08 fall outside the 12-month window
        ("C2", "rates_derivative", Decimal("4000000.00"), 0.8),
        ("C2", "fx_derivative", Decimal("1000000.00"), 0.2),
    ]
    assert [row[:3] for row in rows] == [row[:3] for row in expected]
    assert [row[3] for row in rows] == pytest.approx([row[3] for row in expected])


def test_asset_class_shares_sum_to_one(con):
    sums = con.execute(
        "SELECT sum(share) FROM vic.v_client_asset_class_mix GROUP BY client_id"
    ).fetchall()
    assert [s for (s,) in sums] == pytest.approx([1.0, 1.0])
