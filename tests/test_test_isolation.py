"""The test setup itself: no test may reach the developer's data/vic.duckdb."""

import pytest

from app import data as app_data
from vic import db
from vic.pipeline import daily_run


def test_opening_default_database_fails_the_test():
    with pytest.raises(pytest.fail.Exception, match="real database"):
        db.get_connection(db.DEFAULT_DB_PATH)


def test_modules_that_imported_get_connection_are_guarded_too():
    with pytest.raises(pytest.fail.Exception, match="real database"):
        daily_run.get_connection(db.DEFAULT_DB_PATH)


def test_unconfigured_app_cannot_fall_back_to_default_database():
    """The CI bug: without a fixture the app silently opened data/vic.duckdb."""
    with pytest.raises(pytest.fail.Exception, match="real database"):
        app_data.has_impulses()


def test_app_db_fixture_uses_the_pipeline_built_temp_database(app_db, app_db_path):
    assert app_data.db_path() == app_db_path
    assert app_db_path.name == "vic.duckdb"
    assert db.DEFAULT_DB_PATH.parts[0] not in app_db_path.parts[-2:]
    impulses = app_db.execute("SELECT count(*) FROM vic_app.daily_impulses").fetchone()
    assert impulses[0] > 0


def test_connection_is_read_at_call_time(app_db_path, tmp_path, monkeypatch):
    """No connection is cached across a change of VIC_DB_PATH."""
    monkeypatch.setenv(app_data.DB_PATH_ENV, str(app_db_path))
    assert app_data.has_impulses()

    monkeypatch.setenv(app_data.DB_PATH_ENV, str(tmp_path / "empty.duckdb"))
    assert not app_data.has_impulses()
