"""The public entry point: the landing page, its disclaimer gate, and the demo pages.

`/` is what the poster's QR code opens. It says what the project is, asks the visitor to
acknowledge a disclaimer, and only then offers the three demos under `/app/`. Every
`/app/...` page refuses a visitor who has not acknowledged it and sends them to `/`, so a
link shared past the landing page still passes through the disclaimer. How the
acknowledgement is remembered, and why it is a cookie, is in `pages.py`.

The call page belongs to the call server. In production Caddy routes `/app/call` there
before it can reach this app; the route below only exists for local runs, where the two
servers listen on different ports.
"""

from urllib.parse import parse_qs

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from collection.api import pages
from collection.channels.outbound import SEED_DEMO_CUSTOMER
from collection.channels.session import issue_token
from collection.config import settings


def create_landing_router(chat_enabled: bool) -> APIRouter:
    router = APIRouter(include_in_schema=False)

    @router.get("/", response_class=HTMLResponse)
    def landing(request: Request, next_: str | None = Query(None, alias="next")) -> HTMLResponse:
        html = pages.render(
            "landing.html",
            retention_days=str(settings.transcript_retention_days),
            next=pages.safe_next(next_) or "",
        )
        if pages.acknowledged(request):
            html = pages.keep_section(html, keep="picker", drop="gate")
        else:
            html = pages.keep_section(html, keep="gate", drop="picker")
        # The same URL serves two different pages depending on a cookie; a cache that
        # kept the picker version would hand it to someone who has not read the notice.
        return HTMLResponse(html, headers={"Cache-Control": "no-store", "Vary": "Cookie"})

    @router.post("/aviso")
    async def acknowledge(request: Request) -> RedirectResponse:
        """The disclaimer's button. A plain form post, so it works with scripts off."""
        form = parse_qs((await request.body()).decode("utf-8", "replace"))
        target = pages.safe_next((form.get("next") or [None])[0]) or "/#demos"
        response = RedirectResponse(target, status_code=303)
        pages.remember_acknowledgement(request, response)
        return response

    if chat_enabled:

        @router.get("/app/chat", response_class=HTMLResponse)
        def chat_page(request: Request) -> Response:
            if redirect := pages.gate(request):
                return redirect
            html = pages.render("demo.html")
            if settings.chat_seed_demo:
                # A seeded message is addressed to a customer, and this page is otherwise
                # anonymous -- an anonymous session gets a random id and so an empty
                # mailbox. In demo mode the page is therefore served carrying a token for
                # the fixture customer whose contact was seeded at startup. The rule still
                # holds: the token is minted on the server, and the browser cannot mint
                # one itself.
                html = html.replace(
                    'data-api=""',
                    f'data-api="" data-token="{issue_token(SEED_DEMO_CUSTOMER)}"',
                    1,
                )
            return HTMLResponse(html)

    @router.get("/app/fila", response_class=HTMLResponse)
    def queue_page(request: Request) -> Response:
        if redirect := pages.gate(request):
            return redirect
        return HTMLResponse(pages.render("fila.html"))

    @router.get("/app/call")
    def call_page() -> RedirectResponse:
        # Local runs only: see the module docstring.
        return RedirectResponse(f"{settings.call_public_url}/app/call", status_code=307)

    return router
