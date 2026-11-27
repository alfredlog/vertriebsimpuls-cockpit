"""Entry point: uv run python -m app

Environment: VIC_HOST (default 127.0.0.1), VIC_PORT (8050),
VIC_DEBUG ("1"/"true" to enable, off by default), VIC_DB_PATH (data/vic.duckdb).
"""

import os

from app import app

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8050
TRUE_VALUES = {"1", "true", "yes"}


def main() -> None:
    """Start the Dash server with settings from environment variables."""
    host = os.environ.get("VIC_HOST", DEFAULT_HOST)
    port = int(os.environ.get("VIC_PORT", DEFAULT_PORT))
    debug = os.environ.get("VIC_DEBUG", "").lower() in TRUE_VALUES
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
