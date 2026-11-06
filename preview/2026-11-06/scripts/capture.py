"""Drive the chat demo page and capture screenshots + videos for the preview pack.

usage: uv run --with playwright python -I /tmp/tcc-demo/capture.py OUT_DIR
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
OUT = Path(sys.argv[1])
IMG, VID = OUT / "img", OUT / "video" / "raw"
IMG.mkdir(parents=True, exist_ok=True)
VID.mkdir(parents=True, exist_ok=True)


def widget(page):
    return page.locator("role=dialog[name='Atendimento']")


def say(page, text, wait_reply=True):
    box = page.get_by_role("textbox", name="Mensagem")
    box.click()
    page.keyboard.type(text, delay=45)
    page.wait_for_timeout(400)
    page.keyboard.press("Enter")
    if wait_reply:
        page.wait_for_timeout(600)
        try:
            page.wait_for_function(
                """() => { const h=[...document.querySelectorAll('*')].find(e=>e.shadowRoot);
                           const t=h && h.shadowRoot.querySelector('textarea,input');
                           return (t && !t.disabled) || h.shadowRoot.textContent.includes('transferida') }""",
            timeout=20000,
            )
        except Exception:
            print("no enabled input after", repr(text))
    page.wait_for_timeout(1800)


def shot(page, name, whole_page=False):
    if whole_page:
        page.screenshot(path=IMG / f"{name}.png")
    else:
        widget(page).screenshot(path=IMG / f"{name}.png")
    print("img", name)


def open_chat(page):
    page.goto(f"{BASE}/chat/demo")
    page.wait_for_timeout(1200)
    page.get_by_role("button", name="Abrir atendimento").click()
    widget(page).wait_for()
    page.wait_for_timeout(2000)


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/usr/bin/chromium")

    def context(name, **kw):
        return browser.new_context(
            viewport=kw.pop("viewport", {"width": 1280, "height": 800}),
            device_scale_factor=2,
            locale="pt-BR",
            record_video_dir=str(VID / name),
            record_video_size=kw.pop("video_size", {"width": 1280, "height": 800}),
            **kw,
        )

    # 1. The seeded customer: proactive message, two questions, then a dispute -> person.
    ctx = context("01-conversa")
    page = ctx.new_page()
    open_chat(page)
    shot(page, "01-chat-mensagem-proativa")
    shot(page, "00-pagina-demo-com-widget", whole_page=True)
    say(page, "quais sao os juros e a multa por atraso?")
    shot(page, "02-chat-resposta-juros")
    say(page, "posso parcelar em 6 vezes?")
    shot(page, "03-chat-resposta-parcelamento")
    say(page, "esse valor ta errado, nao e isso que eu devo")
    shot(page, "04-chat-escalacao-humano")
    page.wait_for_timeout(1500)
    ctx.close()

    # 2. Opt-out, on a fresh anonymous visitor so the thread is short and readable.
    ctx = context("02-opt-out")
    page = ctx.new_page()
    page.goto(f"{BASE}/chat/demo")
    page.evaluate("localStorage.clear(); sessionStorage.clear()")
    open_chat(page)
    say(page, "parem de me mandar mensagem")
    shot(page, "05-chat-opt-out")
    page.wait_for_timeout(1500)
    ctx.close()

    # 3. Phone viewport.
    ctx = context("03-celular", viewport={"width": 390, "height": 844},
                  video_size={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    open_chat(page)
    say(page, "como emito a segunda via do boleto?")
    shot(page, "06-chat-celular", whole_page=True)
    ctx.close()

    # 4. The API surface.
    ctx = context("04-api")
    page = ctx.new_page()
    page.goto(f"{BASE}/docs")
    page.wait_for_timeout(2500)
    shot(page, "07-api-swagger", whole_page=True)
    ctx.close()

    browser.close()
