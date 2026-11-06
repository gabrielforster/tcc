"""Capture the console walkthrough and the voice call: 2x screenshots, videos, audio cue log.

usage: uv run --no-project --with playwright python -I capture.py PACK_DIR
(mock pages served at http://127.0.0.1:8090 from PACK_DIR/mock)
"""

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8090"
PACK = Path(sys.argv[1])
IMG, RAW = PACK / "img", PACK / "video" / "raw"
IMG.mkdir(parents=True, exist_ok=True)
RAW.mkdir(parents=True, exist_ok=True)
VIEW = {"width": 1280, "height": 800}


def shot(page, name, full=False):
    page.screenshot(path=IMG / f"{name}.png", full_page=full)
    print("img", name)


def pause(page, ms):
    page.wait_for_timeout(ms)


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/usr/bin/chromium")

    # ---------------------------------------------------------------- console walkthrough
    ctx = browser.new_context(viewport=VIEW, device_scale_factor=2, locale="pt-BR",
                              record_video_dir=str(RAW / "console"), record_video_size=VIEW)
    page = ctx.new_page()
    page.goto(f"{BASE}/console.html#/painel")
    pause(page, 1800)
    shot(page, "20-console-painel")
    page.mouse.move(700, 420)
    pause(page, 2500)
    page.get_by_text("Alto risco", exact=True).nth(1).click()  # segment row -> filtered portfolio
    pause(page, 2200)
    shot(page, "21-console-carteira-alto-risco")
    page.locator("tr.click").nth(2).click()
    pause(page, 3500)
    shot(page, "22-console-cliente-alto-risco")
    page.mouse.wheel(0, 300)
    pause(page, 1500)
    page.goto(f"{BASE}/console.html#/carteira")
    pause(page, 800)
    page.get_by_text("Bom pagador", exact=True).first.click()
    pause(page, 1800)
    shot(page, "23-console-carteira-bom-pagador")
    page.locator("tr.click").nth(3).click()
    pause(page, 3500)
    shot(page, "24-console-cliente-bom-pagador")
    page.goto(f"{BASE}/console.html#/carteira")
    pause(page, 600)
    page.locator(".chip", has_text="Atenção").click()
    pause(page, 1200)
    page.locator("tr.click").nth(5).click()
    pause(page, 2500)
    shot(page, "25-console-cliente-atencao")
    page.locator("nav a[data-r=fila]").click()
    pause(page, 2200)
    shot(page, "26-console-fila-escalacao")
    page.locator("#claim").click()
    pause(page, 2000)
    shot(page, "27-console-conversa-assumida")
    page.locator("#reply").click()
    page.keyboard.type("Oi Marina, aqui é a Paula. Vou conferir o valor com o financeiro e te retorno ainda hoje por aqui.", delay=28)
    pause(page, 500)
    page.get_by_role("button", name="Enviar").click()
    pause(page, 2000)
    page.locator("#resolve").click()
    pause(page, 2200)
    shot(page, "28-console-conversa-resolvida")
    page.locator("nav a[data-r=chamada]").click()
    pause(page, 2200)
    shot(page, "29-console-chamada-registro")
    page.mouse.move(600, 500)
    page.mouse.wheel(0, 500)
    pause(page, 2500)
    ctx.close()

    # full-page stills of the long pages
    ctx = browser.new_context(viewport=VIEW, device_scale_factor=2, locale="pt-BR")
    page = ctx.new_page()
    page.goto(f"{BASE}/console.html#/chamada")
    pause(page, 1500)
    shot(page, "29-console-chamada-registro", full=True)
    ctx.close()

    # ---------------------------------------------------------------- voice call
    ctx = browser.new_context(viewport=VIEW, device_scale_factor=2, locale="pt-BR",
                              record_video_dir=str(RAW / "voz"), record_video_size=VIEW)
    video_start = time.time() * 1000
    page = ctx.new_page()
    page.goto(f"{BASE}/voz.html")
    pause(page, 1200)
    shot(page, "30-voz-antes-de-ligar")
    page.locator(".start button").click()
    page.wait_for_function("window.__t0 !== undefined")
    origin = page.evaluate("performance.timeOrigin + window.__t0")
    offset = origin - video_start

    def at(ms):  # wait until the call clock passes `ms`
        page.wait_for_function(f"performance.now() - window.__t0 > {ms}", timeout=120000, polling=50)

    marks = {}
    for name, cond in [("31-voz-agente-abertura", 5500), ("32-voz-interrupcao", 21500),
                       ("33-voz-leitura-de-volta", 29500), ("34-voz-por-dentro", 47000)]:
        at(cond)
        shot(page, name)
    page.wait_for_function("window.__done === true", timeout=120000)
    pause(page, 2500)
    shot(page, "35-voz-encerrada")
    events = page.evaluate("window.__events")
    ctx.close()
    json.dump({"offset_ms": offset, "events": events}, open(RAW / "voz-audio.json", "w"), indent=1)
    print("offset", round(offset), "events", len(events))
    browser.close()
