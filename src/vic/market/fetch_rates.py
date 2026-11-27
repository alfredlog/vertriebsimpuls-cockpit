"""Euro area AAA government bond yield curve (spot rates) from the ECB Data Portal.

Verified against https://data.ecb.europa.eu/help/api/data (2026-11-27) and a
live request. URL pattern: https://data-api.ecb.europa.eu/service/data/{flowRef}/{key}
with "+" as OR operator inside a key dimension, e.g. for as_of = 2026-11-20:

https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_1Y+SR_2Y+SR_5Y+SR_10Y+SR_30Y?startPeriod=2025-05-20&endPeriod=2026-11-20&format=csvdata&detail=dataonly

Key: B = business daily, U2 = euro area, G_N_A = government bonds rated AAA,
SV_C_YM = Svensson model with continuous compounding, SR_<n>Y = spot rate.
detail=dataonly drops the attribute columns (about 6x smaller response).

If the request fails, the bundled snapshot data/ecb_yield_curve_fallback.csv
is used, so the daily run never depends on the ECB being reachable.
"""

import importlib.resources
import io
import logging
import ssl
import urllib.request
from datetime import date

import certifi
import pandas as pd

logger = logging.getLogger(__name__)

ECB_DATA_URL = "https://data-api.ecb.europa.eu/service/data/YC"
SERIES_KEY_PREFIX = "B.U2.EUR.4F.G_N_A.SV_C_YM"
TENOR_CODES = {"SR_1Y": 1, "SR_2Y": 2, "SR_5Y": 5, "SR_10Y": 10, "SR_30Y": 30}
HISTORY_MONTHS = 18
TIMEOUT_SECONDS = 10
RATE_DECIMALS = 4
FALLBACK_FILE = "ecb_yield_curve_fallback.csv"
RATE_COLUMNS = ["rate_date", "tenor_years", "rate_pct"]


def history_start(as_of: date) -> date:
    """Return the first day of the history window ending at as_of."""
    return (pd.Timestamp(as_of) - pd.DateOffset(months=HISTORY_MONTHS)).date()


def build_url(as_of: date) -> str:
    """Return the ECB request URL for all tenors over the history window."""
    key = SERIES_KEY_PREFIX + "." + "+".join(TENOR_CODES)
    return (
        f"{ECB_DATA_URL}/{key}"
        f"?startPeriod={history_start(as_of).isoformat()}"
        f"&endPeriod={as_of.isoformat()}"
        "&format=csvdata&detail=dataonly"
    )


def parse_ecb_csv(text: str) -> pd.DataFrame:
    """Normalize ECB csvdata to rate_date, tenor_years, rate_pct."""
    raw = pd.read_csv(
        io.StringIO(text), usecols=["DATA_TYPE_FM", "TIME_PERIOD", "OBS_VALUE"]
    )
    raw = raw[raw["DATA_TYPE_FM"].isin(TENOR_CODES)].dropna(subset=["OBS_VALUE"])
    rates = pd.DataFrame(
        {
            "rate_date": pd.to_datetime(raw["TIME_PERIOD"]).dt.date,
            "tenor_years": raw["DATA_TYPE_FM"].map(TENOR_CODES),
            "rate_pct": raw["OBS_VALUE"].astype(float).round(RATE_DECIMALS),
        }
    )
    return rates.sort_values(["rate_date", "tenor_years"]).reset_index(drop=True)


def load_fallback() -> pd.DataFrame:
    """Return the bundled yield curve snapshot."""
    path = importlib.resources.files("vic.market").joinpath("data", FALLBACK_FILE)
    return parse_ecb_csv(path.read_text(encoding="utf-8"))


def _within_window(rates: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Keep rates in the history window; the snapshot may extend past as_of."""
    in_window = (rates["rate_date"] >= history_start(as_of)) & (
        rates["rate_date"] <= as_of
    )
    return rates[in_window].reset_index(drop=True)


def _ssl_context() -> ssl.SSLContext:
    """Return a verifying TLS context that uses certifi's CA bundle.

    Some Python builds (e.g. python.org on macOS) ship without system CA
    certificates, so the default context fails to verify the ECB server.
    """
    return ssl.create_default_context(cafile=certifi.where())


def fetch_rates(as_of: date, timeout: float = TIMEOUT_SECONDS) -> pd.DataFrame:
    """Fetch the yield curve from the ECB; fall back to the bundled snapshot."""
    url = build_url(as_of)
    try:
        with urllib.request.urlopen(
            url, timeout=timeout, context=_ssl_context()
        ) as response:
            rates = parse_ecb_csv(response.read().decode("utf-8"))
    # OSError covers URLError, HTTPError and timeouts; ValueError covers a
    # response that is not the expected CSV.
    except (OSError, ValueError) as error:
        logger.warning("ECB request failed (%s), using fallback snapshot", error)
        return _within_window(load_fallback(), as_of)

    if rates.empty:
        logger.warning("ECB returned no rates, using fallback snapshot")
        return _within_window(load_fallback(), as_of)
    return _within_window(rates, as_of)
