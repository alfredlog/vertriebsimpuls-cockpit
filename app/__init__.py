"""Dash app of the Vertriebsimpuls-Cockpit (multi-page, Tailwind via Play CDN)."""

from dash import Dash, page_container

from app.shell import shell as build_shell

# Official Play CDN snippet (tailwindcss.com/docs/installation/play-cdn, v4).
# The CDN build is meant for development; fine for a local demo, not production.
TAILWIND_CDN = "https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"

app = Dash(
    __name__,
    use_pages=True,
    pages_folder="pages",
    external_scripts=[TAILWIND_CDN],
    title="Vertriebsimpuls-Cockpit",
    suppress_callback_exceptions=True,
)
app.layout = build_shell(page_container)
server = app.server
