"""Take the README screenshots (docs/img/*.png) from a freshly built demo database.

Usage: uv run python scripts/take_screenshots.py
One-time setup: uv run playwright install chromium

Steps: build a demo database in a temp directory with the real pipeline
(never data/vic.duckdb), start the Dash app on a free port against it, wait
until it answers, then let headless Chromium wait for each page to be really
rendered (Tailwind applied, tables filled, every Plotly chart drawn) before
taking the screenshot. The app process is always stopped, also on errors.
The demo feedback is fictitious and only fills the monitoring charts.
"""

import logging
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
from playwright.sync_api import Page, sync_playwright

from vic.data.generate import AS_OF_DEFAULT
from vic.db import get_connection, record_feedback
from vic.market.fetch_rates import load_fallback
from vic.pipeline.daily_run import run_daily
from vic.reco.evaluate import run as run_evaluation

logger = logging.getLogger("take_screenshots")

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "docs" / "img"
VIEWPORT = {"width": 1440, "height": 900}
MAX_FILE_BYTES = 400 * 1024
STARTUP_TIMEOUT_S = 60
RENDER_TIMEOUT_MS = 30_000
SETTLE_MS = 300  # one short pause after all checks, for the last paint
RUN_HOUR = 6

# Four weekly runs give the acceptance chart several weeks to show.
DEMO_WEEKS = 4
DEMO_SEED = 7
FEEDBACK_SHARE = 0.4
# Fictitious acceptance tendencies, only to make the demo charts readable.
DEMO_ACCEPT_PROBABILITY = {
    "maturity": 0.7,
    "rate_move": 0.5,
    "reactivation": 0.45,
    "concentration": 0.35,
    "recommendation": 0.25,
}


@dataclass(frozen=True)
class Shot:
    file_name: str
    path: str
    expected_text: str
    plots: int
    table_selector: str | None
    full_page: bool


# --- Demo database ----------------------------------------------------------------


def demo_run_dates(as_of: date, weeks: int) -> list[date]:
    """Weekly run dates ending at as_of, oldest first."""
    return [as_of - timedelta(days=7 * offset) for offset in reversed(range(weeks))]


def add_demo_feedback(con, run_date: date, rng: np.random.Generator) -> int:
    """Record fictitious accept/reject decisions for part of one day's impulses."""
    impulses = con.execute(
        "SELECT impulse_id, signal_type FROM vic_app.daily_impulses "
        "WHERE run_date = ? ORDER BY impulse_id",
        [run_date],
    ).fetchall()
    ts = datetime.combine(run_date, datetime.min.time()) + timedelta(hours=RUN_HOUR + 3)
    written = 0
    for impulse_id, signal_type in impulses:
        if rng.random() >= FEEDBACK_SHARE:
            continue
        accepted = rng.random() < DEMO_ACCEPT_PROBABILITY[signal_type]
        status = "angenommen" if accepted else "abgelehnt"
        record_feedback(con, impulse_id, run_date, status, ts)
        written += 1
    return written


def build_demo_database(
    path: Path, as_of: date = AS_OF_DEFAULT, weeks: int = DEMO_WEEKS
) -> str:
    """Run pipeline + evaluation for each week, add demo feedback.

    Returns a client with a maturity impulse on the last day (for Kunde 360°).
    """
    rng = np.random.default_rng(DEMO_SEED)
    rates = load_fallback()
    con = get_connection(path)
    try:
        for run_date in demo_run_dates(as_of, weeks):
            run_ts = datetime.combine(run_date, datetime.min.time()) + timedelta(
                hours=RUN_HOUR
            )
            run_daily(con, run_date, run_ts, market_rates=rates)
            run_evaluation(con, run_ts, path.parent / "evaluation.md")
            written = add_demo_feedback(con, run_date, rng)
            logger.info("demo run %s: %d feedback rows", run_date, written)
        row = con.execute(
            "SELECT client_id FROM vic_app.daily_impulses "
            "WHERE run_date = ? AND signal_type = 'maturity' "
            "ORDER BY score DESC, client_id LIMIT 1",
            [as_of],
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise RuntimeError(f"No client with a maturity impulse on {as_of}")
    return row[0]


# --- App process ----------------------------------------------------------------------


def free_port() -> int:
    """Ask the OS for a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_http(
    url: str, process: subprocess.Popen, log_path: Path, timeout_s: float
) -> None:
    """Poll url until it answers 200; fail early if the app process died."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"App exited with code {process.returncode} before answering.\n"
                f"Log ({log_path}):\n{log_path.read_text(errors='replace')[-3000:]}"
            )
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(0.5)
    raise RuntimeError(
        f"App did not answer at {url} within {timeout_s:.0f} s.\n"
        f"Log ({log_path}):\n{log_path.read_text(errors='replace')[-3000:]}"
    )


def start_app(db_path: Path, port: int, log_path: Path) -> subprocess.Popen:
    """Start `python -m app` against db_path; output goes to log_path."""
    env = {
        **os.environ,
        "VIC_DB_PATH": str(db_path),
        "VIC_PORT": str(port),
        "VIC_HOST": "127.0.0.1",
        "VIC_DEBUG": "0",
    }
    log = log_path.open("w")
    return subprocess.Popen(
        [sys.executable, "-m", "app"],
        cwd=REPO_ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )


def stop_app(process: subprocess.Popen) -> None:
    """Terminate the app; kill it if it does not stop within 10 s."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


# --- Rendering checks -------------------------------------------------------------------

# Tailwind (Play CDN) generates CSS in the browser; without it the sidebar has no
# background. Failing here is better than saving an unstyled screenshot.
TAILWIND_READY_JS = """() => {
    const aside = document.querySelector('aside');
    return !!aside && getComputedStyle(aside).backgroundColor !== 'rgba(0, 0, 0, 0)';
}"""

PLOTS_READY_JS = """(expected) => {
    const plots = [...document.querySelectorAll('.js-plotly-plot')];
    return plots.length >= expected && plots.every(
        (el) => el._fullLayout && el._fullData && el.querySelector('.main-svg')
    );
}"""


def wait_until_rendered(page: Page, shot: Shot) -> None:
    """Wait for network idle, styles, expected text, table rows and all charts."""
    page.wait_for_load_state("networkidle", timeout=RENDER_TIMEOUT_MS)
    page.wait_for_function(TAILWIND_READY_JS, timeout=RENDER_TIMEOUT_MS)
    page.get_by_text(shot.expected_text, exact=False).first.wait_for(
        timeout=RENDER_TIMEOUT_MS
    )
    if shot.table_selector:
        page.locator(shot.table_selector).first.wait_for(timeout=RENDER_TIMEOUT_MS)
    if shot.plots:
        page.wait_for_function(
            PLOTS_READY_JS, arg=shot.plots, timeout=RENDER_TIMEOUT_MS
        )
    page.evaluate("document.fonts.ready")
    page.wait_for_load_state("networkidle", timeout=RENDER_TIMEOUT_MS)
    page.wait_for_timeout(SETTLE_MS)


def shots(client_id: str) -> list[Shot]:
    return [
        Shot("tagesimpulse.png", "/", "Impulse heute", 0, "#impulse-table td", False),
        Shot(
            "kunde360.png",
            f"/kunde/{client_id}",
            "Offene Positionen",
            2,
            "#client-positions td",
            True,
        ),
        Shot("markt.png", "/markt", "Zinsstrukturkurve", 1, None, True),
        Shot(
            "monitoring.png",
            "/monitoring",
            "Datenqualitätsprüfungen",
            2,
            "#reco-table td",
            True,
        ),
    ]


def capture(page: Page, shot: Shot, target: Path) -> None:
    """Screenshot the viewport, or the whole page for full_page shots.

    Playwright's full_page mode does not change what 100vh means, so the
    sticky h-screen sidebar would stop at 900 px. Instead the viewport is
    grown to the page height (width unchanged, so charts keep their layout)
    and reset afterwards.
    """
    if not shot.full_page:
        page.screenshot(path=str(target))
        return
    height = page.evaluate("document.documentElement.scrollHeight")
    page.set_viewport_size(
        {"width": VIEWPORT["width"], "height": max(height, VIEWPORT["height"])}
    )
    try:
        page.wait_for_function(
            PLOTS_READY_JS, arg=shot.plots, timeout=RENDER_TIMEOUT_MS
        )
        page.wait_for_timeout(SETTLE_MS)
        page.screenshot(path=str(target))
    finally:
        page.set_viewport_size(VIEWPORT)


def take_screenshots(base_url: str, client_id: str, output_dir: Path) -> list[Path]:
    """Capture every page; raise if a page does not render or a file is too big."""
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
            page = context.new_page()
            for shot in shots(client_id):
                page.goto(base_url + shot.path, wait_until="domcontentloaded")
                wait_until_rendered(page, shot)
                page.mouse.move(0, 0)  # no hover state in the picture
                target = output_dir / shot.file_name
                capture(page, shot, target)
                size = target.stat().st_size
                if size >= MAX_FILE_BYTES:
                    raise RuntimeError(f"{target} is {size // 1024} KB (limit 400 KB)")
                logger.info("%s: %d KB", target.relative_to(REPO_ROOT), size // 1024)
                written.append(target)
        finally:
            browser.close()
    return written


def main() -> None:
    """Build demo data, start the app, take all screenshots, stop the app."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with tempfile.TemporaryDirectory(prefix="vic-screenshots-") as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / "vic.duckdb"
        logger.info("building demo database in %s", db_path)
        client_id = build_demo_database(db_path)

        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        log_path = tmp_path / "app.log"
        process = start_app(db_path, port, log_path)
        try:
            wait_for_http(base_url + "/", process, log_path, STARTUP_TIMEOUT_S)
            logger.info("app answers at %s", base_url)
            take_screenshots(base_url, client_id, OUTPUT_DIR)
        finally:
            stop_app(process)
            logger.info("app stopped")


if __name__ == "__main__":
    main()
