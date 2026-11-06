"""Presentation-only launcher: the real server, with a scripted stand-in for the LLM.

Nothing here lives in the repo. Retrieval, intent classification, escalation, opt-out and
delivery tracking are the real code paths; only the text-generation call is canned, so the
chat bubbles read like the model's answer instead of the extractive fallback's raw chunks.
Run from the repo root:  uv run python preview/2026-11-06/scripts/serve_demo.py
"""

import time
import unicodedata

from collection.llm.client import Completion, LLMClient
from collection.llm.responder import LLMResponder
import collection.api.chat as chat


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c))


# (keywords that must all appear, reply). First match wins. Every figure below is copied
# from knowledge/ (faq.md and the instalment table), so the canned text stays grounded.
REPLIES = [
    (("juros",), "Em caso de atraso, há multa de 2% sobre o valor do título e juros de 1% ao "
     "mês, calculados proporcionalmente aos dias em atraso. Se quiser, posso simular o valor "
     "atualizado ou já te passar as opções de parcelamento."),
    (("multa",), "A multa por atraso é de 2% sobre o valor do título, e os juros são de 1% ao "
     "mês, proporcionais aos dias de atraso."),
    (("parcel",), "Pode sim! Para atrasos entre 8 e 30 dias, como o seu, dá para parcelar em "
     "até 6 vezes, com entrada mínima de 10% e juros de 1% ao mês. Se preferir pagar à vista, "
     "você tem 10% de desconto. Quer que eu monte a proposta em 6 parcelas?"),
    (("6",), "Sim, em 6 parcelas: entrada de 10% e o restante em 6 vezes com juros de 1% ao "
     "mês. Posso gerar o acordo para você confirmar?"),
    (("pix",), "Aceitamos Pix, boleto, cartão de crédito e transferência. O Pix é confirmado "
     "em poucos minutos; o boleto pode levar até 2 dias úteis para a baixa."),
    (("segunda via",), "Claro! A segunda via sai com vencimento no próximo dia útil e com o "
     "valor atualizado até essa data. Posso gerar agora?"),
    (("boleto",), "Posso emitir a segunda via do boleto por aqui, com vencimento no próximo "
     "dia útil e valor atualizado até essa data."),
    (("paguei",), "Obrigado por avisar! Envie o comprovante por aqui que eu suspendo a "
     "cobrança enquanto a baixa é conferida. Pagamentos por boleto podem levar até 2 dias "
     "úteis para aparecer."),
    (("negativ",), "Depois da quitação ou da formalização de um acordo, a baixa da "
     "negativação é solicitada em até 5 dias úteis."),
    (("horario",), "O contato de cobrança acontece de segunda a sexta, das 8h às 20h, e aos "
     "sábados das 8h às 14h. Nunca em domingos e feriados."),
]
GENERIC = ("Posso te ajudar com o valor em aberto, as condições de parcelamento, a segunda "
           "via ou o envio de um comprovante. Por onde prefere começar?")


class DemoClient(LLMClient):
    name = "demo"
    model = "demo-scripted"

    def complete(self, system: str, user: str) -> Completion:
        message = user.rsplit("MENSAGEM DO CLIENTE:", 1)[-1].split("\n\n", 1)[0]
        msg = _norm(message)
        start = time.perf_counter()
        time.sleep(0.9)  # a model does not answer in 2 ms; keep the typing dots visible
        text = next((reply for keys, reply in REPLIES if all(k in msg for k in keys)), GENERIC)
        return Completion(text=text, model=self.model,
                          latency_ms=(time.perf_counter() - start) * 1000)


chat.default_responder = lambda: LLMResponder(client=DemoClient())

if __name__ == "__main__":
    import sys
    from collection.cli import app

    sys.argv = ["collection", "serve", "--seed-demo", "--port", "8000"]
    app()
