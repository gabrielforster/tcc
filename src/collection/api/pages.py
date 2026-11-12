"""The public pages' shared parts: the banner, the stylesheet and the disclaimer gate.

Two processes serve pages -- the API (landing, chat, contact queue) and the call server
(the call) -- and the banner has to read the same on every one of them. So the banner text
lives here once, every page is rendered through `render`, and a test can hold each page to
the exact sentence.

**The gate is a cookie, not localStorage.** The visitor must read the disclaimer before
any demo, and a page opened straight from a shared link must send them back to it. Only a
cookie reaches the server with the request, so only a cookie lets the server answer
`/app/...` with a redirect before the page exists; with localStorage the page would load,
then a script would bounce it, and with scripts off it would not bounce at all. The cookie
is also the one store both servers can read: they sit on the same host in production, and
cookies ignore the port, so locally too. It records an acknowledgement and nothing else --
strictly necessary, no identifier, no tracking -- and it is `HttpOnly` because no script
ever needs it.
"""

import re
from pathlib import Path
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import RedirectResponse

WEB_DIR = Path(__file__).resolve().parents[1] / "web"

#: Exactly this sentence, on every page. Production holds synthetic data only, and the
#: banner is what says so to someone who skipped the disclaimer's other paragraphs.
BANNER_TEXT = "Dados sintéticos — nenhum dado de valores, ou pessoas, reais"

ACK_COOKIE = "aviso_lido"
#: Long enough to cover the presentation weeks, short enough that a borrowed phone does
#: not carry the acknowledgement for good.
ACK_MAX_AGE = 60 * 60 * 24 * 30

_BANNER = (
    '<div class="banner" role="note" aria-label="Aviso permanente">'
    f'<span class="banner-mark" aria-hidden="true">◆</span> {BANNER_TEXT}</div>'
)
_MASTHEAD = (
    '<header class="masthead"><a class="brand" href="/" aria-label="Página inicial">'
    '<span class="brand-mark" aria-hidden="true">c</span>'
    '<span class="brand-name">cobrança<em>assistida</em></span></a>'
    '<span class="brand-tag">TCC · Católica SC</span></header>'
)


def render(name: str, **values: str) -> str:
    """A page from `web/`, with the shared parts and `{{key}}` values filled in.

    Plain substitution rather than a template engine: the pages are static apart from a
    handful of values, and every value passed here is the server's own, never input.
    """
    html = (WEB_DIR / name).read_text(encoding="utf-8")
    shared = {
        "site_css": (WEB_DIR / "site.css").read_text(encoding="utf-8"),
        "banner": _BANNER,
        "masthead": _MASTHEAD,
    }
    for key, value in {**shared, **values}.items():
        html = html.replace("{{" + key + "}}", value)
    return html


def keep_section(html: str, keep: str, drop: str) -> str:
    """Remove the `<!-- drop -->...<!-- /drop -->` block and unwrap the `keep` one.

    Server-side, so the demo picker is simply absent from a page whose visitor has not
    acknowledged the disclaimer, rather than present and hidden by a stylesheet.
    """
    html = re.sub(rf"<!-- {drop} -->.*?<!-- /{drop} -->", "", html, flags=re.DOTALL)
    return html.replace(f"<!-- {keep} -->", "").replace(f"<!-- /{keep} -->", "")


def acknowledged(request: Request) -> bool:
    return request.cookies.get(ACK_COOKIE) == "1"


def safe_next(path: str | None) -> str | None:
    """Only a demo page of this site; anything else would make `/` an open redirect."""
    if path and path.startswith("/app/") and not path.startswith("//") and "\\" not in path:
        return path
    return None


def gate(request: Request) -> RedirectResponse | None:
    """A redirect to the disclaimer when it has not been acknowledged, else `None`.

    Relative, so the browser stays on whatever host it asked: on the call server in
    production `/` is routed back to the API by Caddy.
    """
    if acknowledged(request):
        return None
    return RedirectResponse(f"/?next={quote(request.url.path)}", status_code=303)


def remember_acknowledgement(request: Request, response: RedirectResponse) -> None:
    # Behind Caddy the app sees plain HTTP; the proxy's header says what the browser used.
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    response.set_cookie(
        ACK_COOKIE,
        "1",
        max_age=ACK_MAX_AGE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=scheme == "https",
    )
