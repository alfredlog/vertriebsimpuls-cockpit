"""Rule tests on small hand-made scenarios.

as_of = 2026-06-30: as_of + 30 = 2026-07-30, as_of - 14 = 2026-06-16,
as_of - 90 = 2026-04-01, as_of - 180 = 2026-01-01, 12 months back = 2025-06-30.
"""

import re
from datetime import date

import pandas as pd
import pytest

from vic.data.generate import AS_OF_DEFAULT, generate_all, generate_products
from vic.db import get_connection, load_all
from vic.market.fetch_rates import load_fallback
from vic.signals.rules import (
    SIGNAL_COLUMNS,
    concentration_rule,
    format_fact,
    maturity_rule,
    rate_move_rule,
    reactivation_rule,
    run_all_rules,
)

AS_OF = date(2026, 6, 30)
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def load(clients, trades=(), positions=(), rates=(), as_of=AS_OF, risk_profiles=None):
    """Load a scenario; clients are (client_id, segment, has_floating_rate_debt).

    risk_profiles maps client_id -> profile; unlisted clients are 'balanced'.
    """
    client_df = pd.DataFrame(
        clients, columns=["client_id", "segment", "has_floating_rate_debt"]
    )
    client_df["name"] = "Kunde " + client_df["client_id"]
    client_df["region"] = "Nord"
    client_df["aum_eur"] = 100e6
    profiles = risk_profiles or {}
    client_df["risk_profile"] = [
        profiles.get(client_id, "balanced") for client_id in client_df["client_id"]
    ]
    client_df["sales_rep_id"] = "R01"

    trade_df = pd.DataFrame(
        trades, columns=["trade_date", "client_id", "product_id", "notional_eur"]
    )
    trade_df["trade_date"] = pd.to_datetime(trade_df["trade_date"])
    trade_df["trade_id"] = [f"T{i:03d}" for i in range(len(trade_df))]

    position_df = pd.DataFrame(
        positions,
        columns=[
            "client_id",
            "product_id",
            "notional_eur",
            "start_date",
            "maturity_date",
        ],
    )
    position_df["start_date"] = pd.to_datetime(position_df["start_date"])
    position_df["maturity_date"] = pd.to_datetime(position_df["maturity_date"])
    position_df["position_id"] = [f"POS{i:03d}" for i in range(len(position_df))]

    rate_df = pd.DataFrame(rates, columns=["rate_date", "tenor_years", "rate_pct"])
    rate_df["rate_date"] = pd.to_datetime(rate_df["rate_date"]).dt.date

    data = {
        "sales_reps": pd.DataFrame(
            {"sales_rep_id": ["R01"], "name": ["Anna Becker"], "region": ["Nord"]}
        ),
        "products": generate_products(),
        "clients": client_df,
        "trades": trade_df,
        "positions": position_df,
    }
    con = get_connection(":memory:")
    load_all(con, data, as_of, rate_df)
    return con


def assert_numbers_come_from_facts(signals: pd.DataFrame) -> None:
    for row in signals.itertuples():
        fact_text = " ".join(format_fact(k, v) for k, v in row.facts.items())
        reason_numbers = set(NUMBER.findall(row.reason_de))
        assert reason_numbers <= set(NUMBER.findall(fact_text)), row.reason_de


def compact(signals: pd.DataFrame) -> list[tuple]:
    return list(
        signals[["client_id", "product_id", "reason_de"]].itertuples(
            index=False, name=None
        )
    )


# --- maturity ----------------------------------------------------------------


def test_maturity_fires_up_to_exactly_30_days():
    con = load(
        clients=[("C1", "cooperative_bank", False)],
        trades=[
            ("2026-01-10", "C1", "P004", 1e6),
            ("2026-02-10", "C1", "P004", 1e6),
            ("2026-03-10", "C1", "P003", 5e6),
        ],
        positions=[
            ("C1", "P003", 3e6, "2021-07-30", "2026-07-30"),  # exactly 30 days
            ("C1", "P001", 2e6, "2016-07-01", "2026-07-01"),  # 1 day
        ],
    )
    signals = maturity_rule(con, AS_OF)

    assert list(signals.columns) == SIGNAL_COLUMNS
    assert compact(signals) == [
        (
            "C1",
            "P001",  # no government bond trades: reinvest in the same product
            "Fälligkeit am 01.07.2026: 2,0 Mio. EUR Bundesanleihe 10J"
            " – Wiederanlage ansprechen.",
        ),
        (
            "C1",
            "P004",  # most traded covered bond (2 trades vs 1)
            "Fälligkeit am 30.07.2026: 3,0 Mio. EUR Pfandbrief 5J"
            " – Wiederanlage in Pfandbrief 10J ansprechen.",
        ),
    ]
    assert signals["signal_strength"].tolist() == pytest.approx([1 - 0.5 / 30, 0.5])
    assert_numbers_come_from_facts(signals)


def test_maturity_does_not_fire_after_31_days_or_when_matured():
    con = load(
        clients=[("C1", "cooperative_bank", False)],
        positions=[
            ("C1", "P003", 3e6, "2021-07-31", "2026-07-31"),  # 31 days
            ("C1", "P003", 3e6, "2021-06-30", "2026-06-30"),  # matured on as_of
        ],
    )
    assert maturity_rule(con, AS_OF).empty


# --- rate_move ---------------------------------------------------------------


def test_rate_move_rising_proposes_swap_to_floating_clients_only():
    con = load(
        clients=[("C1", "corporate", True), ("C2", "corporate", False)],
        rates=[
            ("2026-06-16", 5, 2.00),
            ("2026-06-30", 5, 2.25),  # +25 bp
            ("2026-06-16", 10, 2.50),
            ("2026-06-30", 10, 2.60),  # +10 bp
        ],
    )
    signals = rate_move_rule(con, AS_OF)

    assert compact(signals) == [
        (
            "C1",
            "P008",
            "5J-Zins in 14 Tagen um +25 bp gestiegen;"
            " variable Finanzierung – Zinsswap 5J ansprechen.",
        )
    ]
    assert signals["signal_strength"].tolist() == pytest.approx([25 / 40])
    assert_numbers_come_from_facts(signals)


def test_rate_move_falling_proposes_cap():
    con = load(
        clients=[("C1", "corporate", True)],
        rates=[
            ("2026-06-16", 5, 2.00),
            ("2026-06-30", 5, 1.90),  # -10 bp
            ("2026-06-16", 10, 2.50),
            ("2026-06-30", 10, 2.20),  # -30 bp
        ],
    )
    signals = rate_move_rule(con, AS_OF)

    assert compact(signals) == [
        (
            "C1",
            "P010",
            "10J-Zins in 14 Tagen um -30 bp gefallen;"
            " variable Finanzierung – Zinscap 3J ansprechen.",
        )
    ]
    assert_numbers_come_from_facts(signals)


def test_rate_move_does_not_fire_at_exactly_20_bp():
    con = load(
        clients=[("C1", "corporate", True)],
        rates=[
            ("2026-06-16", 5, 2.00),
            ("2026-06-30", 5, 2.20),  # exactly +20 bp
            ("2026-06-16", 10, 2.50),
            ("2026-06-30", 10, 2.30),  # exactly -20 bp
            ("2026-06-16", 2, 2.00),
            ("2026-06-30", 2, 2.50),  # +50 bp, but 2Y is not watched
        ],
    )
    assert rate_move_rule(con, AS_OF).empty


# --- reactivation ------------------------------------------------------------


def test_reactivation_fires_after_91_days_but_not_at_exactly_90():
    con = load(
        clients=[
            ("C1", "corporate", False),
            ("C2", "corporate", False),
            ("C3", "corporate", False),
            ("C4", "corporate", False),
        ],
        trades=[
            ("2026-03-31", "C1", "P012", 2e6),  # 91 days ago
            ("2026-04-01", "C2", "P012", 2e6),  # exactly 90 days ago
            ("2025-12-31", "C3", "P012", 2e6),  # 181 days: inactive too long
            ("2026-06-01", "C4", "P012", 2e6),  # still active
        ],
    )
    signals = reactivation_rule(con, AS_OF)

    assert compact(signals) == [
        (
            "C1",
            None,
            "Seit 91 Tagen kein Geschäft (letztes am 31.03.2026),"
            " davor 2,0 Mio. EUR in 90 Tagen – Kontakt reaktivieren.",
        )
    ]
    assert signals["signal_strength"].tolist() == pytest.approx([1 - 0.5 / 90])
    assert_numbers_come_from_facts(signals)


# --- concentration -----------------------------------------------------------

# Segment of 4 cooperative banks. Penetration (clients that traded the class):
# covered 2/4, fund 3/4, government 1/4, money market 1/4.
CONCENTRATION_TRADES = [
    ("2026-03-01", "C1", "P003", 7e6),  # C1: 70 % covered bonds -> fires
    ("2026-03-01", "C1", "P001", 3e6),
    ("2026-03-01", "C2", "P003", 6e6),  # C2: exactly 60 % -> must not fire
    ("2026-03-01", "C2", "P015", 4e6),
    ("2026-03-01", "C3", "P015", 1e6),  # C3: 50 / 50
    ("2026-03-01", "C3", "P012", 1e6),
    ("2026-03-01", "C4", "P016", 2e6),  # C4: 100 % funds -> fires
]


def test_concentration_proposes_popular_missing_class():
    con = load(
        clients=[(f"C{i}", "cooperative_bank", False) for i in range(1, 5)],
        trades=CONCENTRATION_TRADES,
    )
    signals = concentration_rule(con, AS_OF)

    assert compact(signals) == [
        (
            "C1",
            "P015",  # fund: 3 of 4 clients, Renten-ETF is the segment's favourite
            "70 % des Volumens der letzten 12 Monate entfallen auf Pfandbriefe;"
            " 75 % der Kunden im Segment handeln Fonds"
            " – Renten-ETF zur Diversifikation ansprechen.",
        ),
        (
            "C4",
            "P003",
            "100 % des Volumens der letzten 12 Monate entfallen auf Fonds;"
            " 50 % der Kunden im Segment handeln Pfandbriefe"
            " – Pfandbrief 5J zur Diversifikation ansprechen.",
        ),
    ]
    assert signals["signal_strength"].tolist() == pytest.approx([0.625, 1.0])
    assert_numbers_come_from_facts(signals)


def test_concentration_ignores_trades_older_than_12_months():
    con = load(
        clients=[("C1", "cooperative_bank", False), ("C2", "cooperative_bank", False)],
        trades=[
            ("2025-06-30", "C1", "P003", 9e6),  # exactly 12 months back: outside
            ("2026-03-01", "C1", "P003", 1e6),
            ("2026-03-01", "C1", "P001", 1e6),
            ("2026-03-01", "C2", "P015", 1e6),
        ],
    )
    # With the old trade C1 would be 11 of 12 Mio. in covered bonds; inside the
    # window it is 50 / 50 and must not fire. C2 (100 % funds) still fires.
    assert concentration_rule(con, AS_OF)["client_id"].tolist() == ["C2"]


# --- all rules ---------------------------------------------------------------


def test_rules_refuse_a_different_as_of():
    con = load(clients=[("C1", "corporate", False)])
    with pytest.raises(ValueError, match="run_context"):
        run_all_rules(con, date(2026, 7, 1))


def test_run_all_rules_on_empty_scenario_has_columns():
    con = load(clients=[("C1", "corporate", False)])
    signals = run_all_rules(con, AS_OF)
    assert signals.empty
    assert list(signals.columns) == SIGNAL_COLUMNS


def test_run_all_rules_on_generated_data():
    con = get_connection(":memory:")
    load_all(
        con, generate_all(seed=42, as_of=AS_OF_DEFAULT), AS_OF_DEFAULT, load_fallback()
    )
    signals = run_all_rules(con, AS_OF_DEFAULT)

    assert list(signals.columns) == SIGNAL_COLUMNS
    assert set(signals["signal_type"]) <= {
        "maturity",
        "rate_move",
        "reactivation",
        "concentration",
    }
    assert signals["signal_strength"].between(0, 1).all()
    assert signals["reason_de"].str.len().gt(0).all()
    assert_numbers_come_from_facts(signals)


def test_concentration_never_proposes_hedging_products():
    con = load(
        clients=[
            ("C1", "corporate", False),
            ("C2", "corporate", True),
            ("C3", "corporate", True),
        ],
        trades=[
            ("2026-03-01", "C1", "P012", 5e6),  # C1: 100 % money market
            ("2026-03-01", "C2", "P008", 5e6),  # swaps: 2 of 3 clients
            ("2026-03-01", "C3", "P008", 5e6),
            ("2026-03-01", "C3", "P005", 1e6),  # corporate bond: 1 of 3
        ],
    )
    signals = concentration_rule(con, AS_OF)

    # The swap is the most popular class, but C1 has no debt to hedge.
    c1 = signals[signals["client_id"] == "C1"]
    assert c1["product_id"].tolist() == ["P005"]


# --- suitability -------------------------------------------------------------
# Catalog risk levels: corporate bonds P005-P007 = 3, funds P015 = 3, P016 = 4,
# money market = 1. Max level: conservative 2, balanced 3, dynamic 5.


def test_maturity_drops_unsuitable_reinvestment():
    con = load(
        clients=[("C1", "corporate", False), ("C2", "corporate", False)],
        trades=[
            ("2026-03-01", "C1", "P005", 1e6),
            ("2026-03-01", "C2", "P005", 1e6),
        ],
        positions=[
            ("C1", "P006", 1e6, "2021-07-10", "2026-07-10"),
            ("C2", "P006", 1e6, "2021-07-10", "2026-07-10"),
        ],
        risk_profiles={"C1": "conservative"},
    )
    signals = maturity_rule(con, AS_OF)

    # C1 is conservative: neither P005 nor the fallback P006 (both level 3) fit.
    assert signals[["client_id", "product_id"]].values.tolist() == [["C2", "P005"]]


def test_concentration_skips_class_without_suitable_product():
    con = load(
        clients=[(f"C{i}", "cooperative_bank", False) for i in range(1, 5)],
        trades=[
            ("2026-03-01", "C1", "P003", 5e6),  # C1: 100 % covered bonds
            ("2026-03-01", "C2", "P003", 1e6),
            ("2026-03-01", "C2", "P015", 1e6),
            ("2026-03-01", "C3", "P015", 1e6),
            ("2026-03-01", "C3", "P012", 1e6),
            ("2026-03-01", "C4", "P016", 1e6),
            ("2026-03-01", "C4", "P012", 1e6),
        ],
        risk_profiles={"C1": "conservative", "C4": "dynamic"},
    )
    signals = concentration_rule(con, AS_OF)

    # Funds are the most popular class (3 of 4), but P015 (3) and P016 (4) are
    # too risky for C1; money market (2 of 4) is the best suitable class.
    assert compact(signals) == [
        (
            "C1",
            "P012",
            "100 % des Volumens der letzten 12 Monate entfallen auf Pfandbriefe;"
            " 50 % der Kunden im Segment handeln Geldmarktprodukte"
            " – Termingeld 3M zur Diversifikation ansprechen.",
        )
    ]
    assert_numbers_come_from_facts(signals)


def test_concentration_picks_next_suitable_product_in_same_class():
    con = load(
        clients=[(f"C{i}", "institutional", False) for i in range(1, 4)],
        trades=[
            ("2026-03-01", "C1", "P001", 5e6),  # C1: 100 % government bonds
            ("2026-03-01", "C2", "P016", 1e6),  # P016 (level 4) is the favourite
            ("2026-03-01", "C3", "P016", 1e6),
            ("2026-03-01", "C3", "P015", 1e6),
        ],
    )
    signals = concentration_rule(con, AS_OF)

    # C1 is balanced (max 3): Aktien-ETF is too risky, Renten-ETF is offered.
    assert signals.loc[signals["client_id"] == "C1", "product_id"].tolist() == ["P015"]


def test_rate_move_hedge_is_offered_to_conservative_clients():
    con = load(
        clients=[("C1", "corporate", True)],
        rates=[("2026-06-16", 5, 2.00), ("2026-06-30", 5, 2.30)],
        risk_profiles={"C1": "conservative"},
    )
    # Zinsswap 5J has risk level 3, but a hedge lowers the client's risk.
    assert rate_move_rule(con, AS_OF)["product_id"].tolist() == ["P008"]
