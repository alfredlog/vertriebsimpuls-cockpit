"""Synthetic data generator for the Vertriebsimpuls-Cockpit.

All data is fictional. Every random draw goes through a single
``np.random.Generator`` so the whole dataset is reproducible from one seed.

Modelling simplifications (documented in README "Limitations"):
- derivatives are always hedging products (no speculative use),
- risk levels use a simplified 1-5 scale instead of the PRIIPs SRI (1-7),
- positions only contain instruments with a maturity (no funds).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

AS_OF_DEFAULT = date(2026, 11, 20)
HISTORY_MONTHS = 24

# --- Products ---------------------------------------------------------------

PRODUCT_COLUMNS = (
    "product_id",
    "name",
    "asset_class",
    "tenor_years",
    "risk_level",
    "is_hedging_product",
)

PRODUCT_CATALOG = (
    ("P001", "Bundesanleihe 10J", "government_bond", 10, 1, False),
    ("P002", "Bundesobligation 5J", "government_bond", 5, 1, False),
    ("P003", "Pfandbrief 5J", "covered_bond", 5, 1, False),
    ("P004", "Pfandbrief 10J", "covered_bond", 10, 2, False),
    ("P005", "Unternehmensanleihe 5J", "corporate_bond", 5, 3, False),
    ("P006", "Schuldscheindarlehen 5J", "corporate_bond", 5, 3, False),
    ("P007", "Schuldscheindarlehen 7J", "corporate_bond", 7, 3, False),
    ("P008", "Zinsswap 5J", "rates_derivative", 5, 3, True),
    ("P009", "Zinsswap 10J", "rates_derivative", 10, 3, True),
    ("P010", "Zinscap 3J", "rates_derivative", 3, 2, True),
    ("P011", "Devisentermingeschäft 1J", "fx_derivative", 1, 2, True),
    ("P012", "Termingeld 3M", "money_market", 0.25, 1, False),
    ("P013", "Termingeld 12M", "money_market", 1, 1, False),
    ("P014", "Bonuszertifikat 3J", "structured_product", 3, 4, False),
    ("P015", "Renten-ETF", "fund", None, 3, False),
    ("P016", "Aktien-ETF", "fund", None, 4, False),
)

# --- Segments ---------------------------------------------------------------

SEGMENT_SHARE = {
    "cooperative_bank": 0.40,  # Volksbanken / Raiffeisenbanken
    "third_party_bank": 0.15,  # Drittbanken
    "corporate": 0.30,  # Firmenkunden
    "institutional": 0.15,  # insurers, pension funds, Versorgungswerke
}

SEGMENT_MEDIAN_AUM_EUR = {
    "cooperative_bank": 800e6,
    "third_party_bank": 3e9,
    "corporate": 150e6,
    "institutional": 5e9,
}

SEGMENT_FLOATING_DEBT_PROB = {
    "cooperative_bank": 0.05,
    "third_party_bank": 0.10,
    "corporate": 0.60,
    "institutional": 0.0,
}

SEGMENT_RISK_PROFILE_PROBS = {  # order: conservative, balanced, dynamic
    "cooperative_bank": (0.6, 0.3, 0.1),
    "third_party_bank": (0.3, 0.5, 0.2),
    "corporate": (0.4, 0.4, 0.2),
    "institutional": (0.3, 0.5, 0.2),
}
RISK_PROFILES = ("conservative", "balanced", "dynamic")

SEGMENT_TRADES_PER_MONTH = {
    "cooperative_bank": 1.0,
    "third_party_bank": 1.5,
    "corporate": 0.8,
    "institutional": 1.2,
}

# Share of each asset class in a segment's typical demand.
# This is the "hidden truth" the recommender should rediscover.
SEGMENT_ASSET_CLASS_AFFINITY = {
    "cooperative_bank": {
        "government_bond": 0.20,
        "covered_bond": 0.30,
        "corporate_bond": 0.20,
        "rates_derivative": 0.05,
        "fx_derivative": 0.00,
        "money_market": 0.15,
        "structured_product": 0.05,
        "fund": 0.05,
    },
    "third_party_bank": {
        "government_bond": 0.15,
        "covered_bond": 0.20,
        "corporate_bond": 0.20,
        "rates_derivative": 0.15,
        "fx_derivative": 0.05,
        "money_market": 0.15,
        "structured_product": 0.05,
        "fund": 0.05,
    },
    "corporate": {
        "government_bond": 0.02,
        "covered_bond": 0.02,
        "corporate_bond": 0.05,
        "rates_derivative": 0.35,
        "fx_derivative": 0.25,
        "money_market": 0.28,
        "structured_product": 0.00,
        "fund": 0.03,
    },
    "institutional": {
        "government_bond": 0.25,
        "covered_bond": 0.25,
        "corporate_bond": 0.25,
        "rates_derivative": 0.05,
        "fx_derivative": 0.00,
        "money_market": 0.05,
        "structured_product": 0.05,
        "fund": 0.10,
    },
}

# Dirichlet concentration: higher = clients stick closer to their segment.
PREFERENCE_CONCENTRATION = 20.0
# Clients without floating-rate debt rarely buy interest rate hedges.
NO_FLOATING_DEBT_RATES_FACTOR = 0.2

# Fewer trades in summer holidays and December, more at the start of the year.
MONTH_SEASONALITY = {
    1: 1.3, 2: 1.1, 3: 1.1, 4: 1.0, 5: 1.0, 6: 1.0,
    7: 0.9, 8: 0.6, 9: 1.0, 10: 1.0, 11: 1.0, 12: 0.7,
}  # fmt: skip

CHURN_SHARE = 0.10
LEGACY_POSITIONS_PER_CLIENT = 3.0

# --- Names (all invented, must not resemble real institutions) --------------

REGIONS = ("Nord", "Süd", "West", "Ost")

FICTIONAL_PLACES = (
    "Birnfeld", "Kieselbach", "Tannhagen", "Moosbrunn", "Erlenried",
    "Haselgrund", "Weidenstein", "Ulmenhof", "Ahornstedt", "Lärchenfurt",
    "Eibenhausen", "Ginsterberg", "Holunderbach", "Mistelfeld", "Wacholderau",
)  # fmt: skip

SEGMENT_NAME_PREFIXES = {
    "cooperative_bank": ("Volksbank", "Raiffeisenbank", "VR-Bank"),
    "third_party_bank": ("Bankhaus", "Kreditbank", "Privatbank"),
    "corporate": ("Maschinenbau", "Logistik", "Energie", "Chemie", "Handel"),
    "institutional": ("Pensionskasse", "Versorgungswerk", "Versicherung"),
}

REP_FIRST_NAMES = ("Anna", "Jonas", "Lea", "Felix", "Mara", "Tobias", "Sina", "Paul")
REP_LAST_NAMES = ("Becker", "Hoffmann", "Krämer", "Lorenz", "Vogt", "Seidel")


def _deduplicate_names(names: pd.Series) -> pd.Series:
    """Append ' 2', ' 3', ... to repeated names so every name is unique."""
    rank = names.groupby(names).cumcount()
    return names.where(rank == 0, names + " " + (rank + 1).astype(str))


# --- Generators -------------------------------------------------------------


def generate_products() -> pd.DataFrame:
    """Return the static product catalog. Deterministic, no randomness."""
    return pd.DataFrame(list(PRODUCT_CATALOG), columns=list(PRODUCT_COLUMNS))


def generate_sales_reps(rng: np.random.Generator, n: int = 8) -> pd.DataFrame:
    """Create n sales reps, spread evenly over the sales regions."""
    first = rng.choice(REP_FIRST_NAMES, size=n)
    last = rng.choice(REP_LAST_NAMES, size=n)
    reps = pd.DataFrame(
        {
            "sales_rep_id": [f"R{i:02d}" for i in range(1, n + 1)],
            "name": [f"{f} {lname}" for f, lname in zip(first, last, strict=True)],
            "region": [REGIONS[i % len(REGIONS)] for i in range(n)],
        }
    )
    reps["name"] = _deduplicate_names(reps["name"])
    return reps


def generate_clients(
    rng: np.random.Generator, reps: pd.DataFrame, n: int = 300
) -> pd.DataFrame:
    """Create n clients with segment-dependent size, risk profile and debt."""
    segments = list(SEGMENT_SHARE)
    segment = rng.choice(segments, size=n, p=list(SEGMENT_SHARE.values()))

    # Log-normal size: always positive, a few very large clients.
    median_aum = np.array([SEGMENT_MEDIAN_AUM_EUR[s] for s in segment])
    aum_eur = rng.lognormal(mean=np.log(median_aum), sigma=1.0)

    risk_profile = [
        rng.choice(RISK_PROFILES, p=SEGMENT_RISK_PROFILE_PROBS[s]) for s in segment
    ]
    floating_prob = np.array([SEGMENT_FLOATING_DEBT_PROB[s] for s in segment])
    has_floating_rate_debt = rng.random(n) < floating_prob

    # Each client gets a rep from its own region.
    region = rng.choice(REGIONS, size=n)
    reps_by_region = reps.groupby("region")["sales_rep_id"].apply(list).to_dict()
    sales_rep_id = [rng.choice(reps_by_region[r]) for r in region]

    names = pd.Series(
        [
            f"{rng.choice(SEGMENT_NAME_PREFIXES[s])} {rng.choice(FICTIONAL_PLACES)}"
            for s in segment
        ]
    )

    return pd.DataFrame(
        {
            "client_id": [f"C{i:04d}" for i in range(1, n + 1)],
            "name": _deduplicate_names(names),
            "segment": segment,
            "region": region,
            "aum_eur": aum_eur.round(-5),  # round to 100k EUR
            "risk_profile": risk_profile,
            "has_floating_rate_debt": has_floating_rate_debt,
            "sales_rep_id": sales_rep_id,
        }
    )


def client_product_preferences(
    rng: np.random.Generator, clients: pd.DataFrame, products: pd.DataFrame
) -> pd.DataFrame:
    """Return a clients x products matrix of purchase probabilities.

    Segment affinity per asset class is split evenly over the products of
    that class, then each client deviates from it via a Dirichlet draw.
    """
    class_size = products["asset_class"].map(products["asset_class"].value_counts())
    is_rates = (products["asset_class"] == "rates_derivative").to_numpy()

    rows = []
    for client in clients.itertuples():
        affinity = SEGMENT_ASSET_CLASS_AFFINITY[client.segment]
        base = products["asset_class"].map(affinity).to_numpy() / class_size.to_numpy()
        if not client.has_floating_rate_debt:
            base = np.where(is_rates, base * NO_FLOATING_DEBT_RATES_FACTOR, base)
        base = (base + 1e-3) / (base + 1e-3).sum()  # Dirichlet needs alpha > 0
        rows.append(rng.dirichlet(PREFERENCE_CONCENTRATION * base))

    return pd.DataFrame(
        rows, index=clients["client_id"], columns=products["product_id"]
    )


def generate_trades(
    rng: np.random.Generator,
    clients: pd.DataFrame,
    products: pd.DataFrame,
    preferences: pd.DataFrame,
    as_of: date,
    history_months: int = HISTORY_MONTHS,
) -> pd.DataFrame:
    """Simulate trades over the history window.

    Monthly trade counts are Poisson distributed; the rate depends on segment,
    client size and month seasonality. ~10 % of clients churn and stop trading.
    """
    as_of_ts = pd.Timestamp(as_of)
    start = (as_of_ts - pd.DateOffset(months=history_months)).normalize()
    months = pd.date_range(start.replace(day=1), as_of_ts, freq="MS")
    seasonality = np.array([MONTH_SEASONALITY[m.month] for m in months])

    churned = rng.random(len(clients)) < CHURN_SHARE
    churn_window_days = (as_of_ts - start).days - 6 * 30 - 4 * 30
    churn_dates = (
        start
        + pd.DateOffset(months=6)
        + pd.to_timedelta(
            rng.integers(0, churn_window_days, size=len(clients)), unit="D"
        )
    )  # churn between 6 months after start and 4 months before as_of

    product_ids = products["product_id"].to_numpy()
    records = []
    for _i, client in enumerate(clients.itertuples()):
        median = SEGMENT_MEDIAN_AUM_EUR[client.segment]
        size_factor = np.clip((client.aum_eur / median) ** 0.5, 0.3, 3.0)
        rate = SEGMENT_TRADES_PER_MONTH[client.segment] * size_factor * seasonality
        counts = rng.poisson(rate)

        for month, count in zip(months, counts, strict=True):
            if count == 0:
                continue
            days = rng.integers(0, month.days_in_month, size=count)
            dates = month + pd.to_timedelta(days, unit="D")
            chosen = rng.choice(
                product_ids, size=count, p=preferences.loc[client.client_id].to_numpy()
            )
            notional = rng.lognormal(np.log(client.aum_eur * 0.01), 0.8, size=count)
            for d, p, amount in zip(dates, chosen, notional, strict=True):
                records.append((d, client.client_id, p, amount))

    trades = pd.DataFrame(
        records, columns=["trade_date", "client_id", "product_id", "notional_eur"]
    )
    trades = trades[
        (trades["trade_date"] >= start) & (trades["trade_date"] <= as_of_ts)
    ]

    churn_by_client = pd.Series(
        np.where(churned, churn_dates, pd.NaT), index=clients["client_id"]
    )
    churn_date = trades["client_id"].map(churn_by_client)
    trades = trades[churn_date.isna() | (trades["trade_date"] < churn_date)]

    trades = trades.sort_values(["trade_date", "client_id"]).reset_index(drop=True)
    trades["notional_eur"] = trades["notional_eur"].round(-5).clip(lower=100_000)
    trades.insert(0, "trade_id", [f"T{i:06d}" for i in range(1, len(trades) + 1)])
    return trades


def generate_positions(
    rng: np.random.Generator,
    trades: pd.DataFrame,
    clients: pd.DataFrame,
    products: pd.DataFrame,
    preferences: pd.DataFrame,
    as_of: date,
) -> pd.DataFrame:
    """Return open positions with a maturity date after as_of.

    Two sources: trades from the history window that have not matured yet,
    and legacy positions bought before the window (so long-dated products
    like a 5y Pfandbrief can also mature soon).
    """
    as_of_ts = pd.Timestamp(as_of)
    tenor = products.set_index("product_id")["tenor_years"]

    # 1) Positions from simulated trades.
    from_trades = trades[trades["product_id"].map(tenor).notna()].copy()
    months = (from_trades["product_id"].map(tenor) * 12).round().astype(int)
    from_trades["maturity_date"] = [
        d + pd.DateOffset(months=m)
        for d, m in zip(from_trades["trade_date"], months, strict=True)
    ]
    from_trades = from_trades.rename(columns={"trade_date": "start_date"})

    # 2) Legacy positions: maturity spread uniformly over the product's tenor.
    with_tenor = products[products["tenor_years"].notna()]
    legacy = []
    for client in clients.itertuples():
        prefs = preferences.loc[client.client_id, with_tenor["product_id"]].to_numpy()
        for _ in range(rng.poisson(LEGACY_POSITIONS_PER_CLIENT)):
            p = rng.choice(with_tenor["product_id"].to_numpy(), p=prefs / prefs.sum())
            tenor_days = int(tenor[p] * 365)
            maturity = as_of_ts + pd.Timedelta(days=int(rng.integers(1, tenor_days)))
            start = maturity - pd.DateOffset(months=round(tenor[p] * 12))
            amount = rng.lognormal(np.log(client.aum_eur * 0.01), 0.8)
            legacy.append((client.client_id, p, amount, start, maturity))

    legacy_df = pd.DataFrame(
        legacy,
        columns=[
            "client_id",
            "product_id",
            "notional_eur",
            "start_date",
            "maturity_date",
        ],
    )
    legacy_df["notional_eur"] = legacy_df["notional_eur"].round(-5).clip(lower=100_000)

    columns = ["client_id", "product_id", "notional_eur", "start_date", "maturity_date"]
    positions = pd.concat([from_trades[columns], legacy_df], ignore_index=True)
    positions = positions[positions["maturity_date"] > as_of_ts]
    positions = positions.sort_values(["maturity_date", "client_id"]).reset_index(
        drop=True
    )
    positions.insert(
        0, "position_id", [f"POS{i:06d}" for i in range(1, len(positions) + 1)]
    )
    return positions


def generate_all(
    seed: int = 42, as_of: date = AS_OF_DEFAULT
) -> dict[str, pd.DataFrame]:
    """Generate the complete, reproducible synthetic dataset."""
    rng = np.random.default_rng(seed)
    products = generate_products()
    reps = generate_sales_reps(rng)
    clients = generate_clients(rng, reps)
    preferences = client_product_preferences(rng, clients, products)
    trades = generate_trades(rng, clients, products, preferences, as_of)
    positions = generate_positions(rng, trades, clients, products, preferences, as_of)
    return {
        "sales_reps": reps,
        "products": products,
        "clients": clients,
        "trades": trades,
        "positions": positions,
    }
