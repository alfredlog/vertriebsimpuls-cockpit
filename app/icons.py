"""Inline line icons (own simple SVG shapes, no icon package).

Dash has no SVG components, so each icon is an SVG used as a CSS mask on a
span: the icon takes the current text color and follows hover/active states.
"""

from urllib.parse import quote

from dash import html

_PATHS = {
    "impulses": (
        "<path d='M9 6h11M9 12h11M9 18h11'/>"
        "<circle cx='4.5' cy='6' r='1'/><circle cx='4.5' cy='12' r='1'/>"
        "<circle cx='4.5' cy='18' r='1'/>"
    ),
    "client": "<circle cx='12' cy='8' r='4'/><path d='M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7'/>",
    "market": "<path d='M3 3v18h18'/><path d='M7 15l4-4 3 3 5-6'/>",
    "monitoring": "<path d='M3 12h4l3-8 4 16 3-8h4'/>",
    "calendar": (
        "<rect x='3' y='5' width='18' height='16' rx='2'/>"
        "<path d='M3 10h18M8 3v4M16 3v4'/>"
    ),
    "check": "<path d='M5 12l5 5 9-10'/>",
    "cross": "<path d='M6 6l12 12M18 6L6 18'/>",
    "arrow_up": "<path d='M12 19V5M6 11l6-6 6 6'/>",
    "arrow_down": "<path d='M12 5v14M6 13l6 6 6-6'/>",
    "search": "<circle cx='11' cy='11' r='7'/><path d='M20 20l-3.5-3.5'/>",
}

_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' "
    "stroke='black' stroke-width='2' stroke-linecap='round' "
    "stroke-linejoin='round'>{paths}</svg>"
)


def icon(name: str, size_class: str = "w-4 h-4", extra_class: str = "") -> html.Span:
    """Return a decorative icon span colored by the current text color."""
    url = f'url("data:image/svg+xml;utf8,{quote(_SVG.format(paths=_PATHS[name]))}")'
    return html.Span(
        className=f"inline-block shrink-0 bg-current {size_class} {extra_class}",
        style={
            "maskImage": url,
            "WebkitMaskImage": url,
            "maskRepeat": "no-repeat",
            "WebkitMaskRepeat": "no-repeat",
            "maskSize": "contain",
            "WebkitMaskSize": "contain",
        },
        **{"aria-hidden": "true"},
    )
