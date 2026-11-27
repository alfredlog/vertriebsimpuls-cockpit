import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest

from app import data as app_data
from vic import db
from vic.data.generate import AS_OF_DEFAULT
from vic.market.fetch_rates import load_fallback
from vic.pipeline.daily_run import run_daily
from vic.reco.evaluate import run as run_evaluation

RUN_TS = datetime(2026, 11, 20, 6, 0)
_real_get_connection = db.get_connection


def _no_network(*args, **kwargs):
    # RuntimeError is not caught by fetch_rates, so an unmocked call fails
    # loudly instead of silently using the fallback.
    raise RuntimeError("Real network call in tests; mock urlopen instead")


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _no_network)


@pytest.fixture(autouse=True)
def forbid_default_database(monkeypatch, request):
    """Fail any test that opens data/vic.duckdb, the developer's real database.

    Modules import get_connection by name, so every module-level reference to
    it is replaced, not only vic.db.get_connection.
    """
    forbidden = (request.config.rootpath / db.DEFAULT_DB_PATH).resolve()

    def guarded(path=db.DEFAULT_DB_PATH):
        if str(path) != ":memory:" and Path(path).resolve() == forbidden:
            pytest.fail(f"Test opened the real database {forbidden}")
        return _real_get_connection(path)

    for module in list(sys.modules.values()):
        if getattr(module, "get_connection", None) is _real_get_connection:
            monkeypatch.setattr(module, "get_connection", guarded)
    monkeypatch.delenv(app_data.DB_PATH_ENV, raising=False)
    app_data.reset_connection()
    yield
    app_data.reset_connection()


@pytest.fixture(scope="session")
def app_db_path(tmp_path_factory) -> Path:
    """A database file built once by the real pipeline plus one evaluation run."""
    folder = tmp_path_factory.mktemp("app_db")
    path = folder / "vic.duckdb"
    con = _real_get_connection(path)
    try:
        run_daily(con, AS_OF_DEFAULT, RUN_TS, market_rates=load_fallback())
        run_evaluation(con, RUN_TS, folder / "evaluation.md")
    finally:
        con.close()
    return path


@pytest.fixture
def app_db(app_db_path, monkeypatch):
    """Point the app at the pipeline-built database; yield the app's connection.

    Feedback is cleared first, so tests that click buttons stay independent.
    """
    monkeypatch.setenv(app_data.DB_PATH_ENV, str(app_db_path))
    app_data.reset_connection()
    con = app_data.connection()
    con.execute("DELETE FROM vic_app.impulse_feedback")
    yield con
    app_data.reset_connection()


@pytest.fixture
def app_connection():
    """Use a given (e.g. in-memory) connection in the app; reset afterwards."""
    opened = []

    def _use(con):
        opened.append(con)
        app_data.set_connection(con)
        return con

    yield _use
    app_data.reset_connection()
    for con in opened:
        con.close()
