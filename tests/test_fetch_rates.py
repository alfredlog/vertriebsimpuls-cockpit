import io
import ssl
import urllib.error
import urllib.request
from datetime import date

import pytest

from vic.data.generate import AS_OF_DEFAULT
from vic.market import fetch_rates as fr

AS_OF = date(2026, 6, 30)

# Same shape as the ECB csvdata response with detail=dataonly; includes an
# empty observation and a tenor we did not ask for.
SAMPLE_CSV = """\
KEY,FREQ,REF_AREA,CURRENCY,PROVIDER_FM,INSTRUMENT_FM,PROVIDER_FM_ID,DATA_TYPE_FM,TIME_PERIOD,OBS_VALUE
YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y,B,U2,EUR,4F,G_N_A,SV_C_YM,SR_10Y,2026-06-29,2.71262531
YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y,B,U2,EUR,4F,G_N_A,SV_C_YM,SR_10Y,2026-06-30,2.73350257
YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y,B,U2,EUR,4F,G_N_A,SV_C_YM,SR_2Y,2026-06-30,-0.12345678
YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y,B,U2,EUR,4F,G_N_A,SV_C_YM,SR_2Y,2026-06-29,
YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_3Y,B,U2,EUR,4F,G_N_A,SV_C_YM,SR_3Y,2026-06-30,1.5
"""

EXPECTED_ROWS = [
    (date(2026, 6, 29), 10, 2.7126),
    (date(2026, 6, 30), 2, -0.1235),
    (date(2026, 6, 30), 10, 2.7335),
]


def rows(df):
    return list(df.itertuples(index=False, name=None))


def test_build_url_matches_verified_format():
    assert fr.build_url(date(2026, 11, 20)) == (
        "https://data-api.ecb.europa.eu/service/data/YC/"
        "B.U2.EUR.4F.G_N_A.SV_C_YM.SR_1Y+SR_2Y+SR_5Y+SR_10Y+SR_30Y"
        "?startPeriod=2025-05-20&endPeriod=2026-11-20&format=csvdata&detail=dataonly"
    )


def test_parse_sample_csv():
    rates = fr.parse_ecb_csv(SAMPLE_CSV)

    assert list(rates.columns) == fr.RATE_COLUMNS
    assert rows(rates) == EXPECTED_ROWS


def test_fetch_uses_api_response_with_timeout_and_tls(monkeypatch):
    calls = []

    def fake_urlopen(url, timeout, context):
        calls.append((url, timeout, context))
        return io.BytesIO(SAMPLE_CSV.encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    rates = fr.fetch_rates(AS_OF)

    assert rows(rates) == EXPECTED_ROWS
    [(url, timeout, context)] = calls
    assert (url, timeout) == (fr.build_url(AS_OF), 10)
    # certifi must add CAs without weakening verification.
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert context.cert_store_stats()["x509_ca"] > 0


@pytest.mark.parametrize(
    "error",
    [urllib.error.URLError("no route"), TimeoutError("timed out")],
)
def test_fetch_falls_back_when_request_raises(monkeypatch, error):
    def failing_urlopen(url, timeout, context):
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", failing_urlopen)
    rates = fr.fetch_rates(AS_OF_DEFAULT)

    assert rows(rates) == rows(fr.load_fallback())


def test_fetch_falls_back_on_empty_response(monkeypatch):
    header_only = SAMPLE_CSV.splitlines()[0] + "\n"
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda url, timeout, context: io.BytesIO(header_only.encode("utf-8")),
    )
    assert not fr.fetch_rates(AS_OF_DEFAULT).empty


def test_fallback_never_returns_rates_after_as_of(monkeypatch):
    def failing_urlopen(url, timeout, context):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(urllib.request, "urlopen", failing_urlopen)
    rates = fr.fetch_rates(AS_OF)

    # The snapshot runs to 2026-11-20; a backtest at AS_OF must not see later rates.
    assert rates["rate_date"].max() == AS_OF


def test_fallback_snapshot_covers_default_as_of():
    rates = fr.load_fallback()

    assert set(rates["tenor_years"]) == {1, 2, 5, 10, 30}
    assert rates["rate_date"].min() == date(2025, 5, 20)
    assert rates["rate_date"].max() == AS_OF_DEFAULT
    assert not rates.duplicated(["rate_date", "tenor_years"]).any()
