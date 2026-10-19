"""The system prompt and the context block: everything the model is allowed to know.

Two rules shape this file.

**The model sees no debt it was not handed.** The context block is built only from
passages the retriever returned, so an answer that states a figure the knowledge base does
not contain is detectable -- that is what `faithfulness.py` measures. Giving the model the
corpus wholesale would make grounding unmeasurable and the hallucination rate unreportable.

**The legal constraints are instructions, not hopes.** CDC art. 42 forbids exposing the
debtor to ridicule or subjecting them to constraint, and the superendividamento amendments
restrict contact hours. A model that threatens, or that invents a discount beyond the
approval limit in `knowledge/limites-de-alcada.md`, creates real liability -- so the prompt
states the prohibitions explicitly and the escalation path exists for everything else.

`REFUSAL` is a literal token rather than a phrase, because detecting "the model declined"
by matching prose is a string-matching problem that eventually matches a real answer.
"""

from collection.agents.intent import Intent
from collection.rag.index import Retrieved

#: Emitted alone when the sources do not support an answer. Handed to a human.
REFUSAL = "NAO_SEI"

#: What the customer should be told while a person takes over.
HANDOVER = "Vou encaminhar o seu atendimento para uma pessoa da equipe."

SYSTEM_PROMPT = f"""\
Você é o atendente virtual da régua de cobrança. Responde a clientes em português do Brasil,
por mensagem de texto curta (WhatsApp).

REGRA PRINCIPAL: responda **somente** com o que estiver nos TRECHOS fornecidos. Os trechos são
a sua única fonte. Se eles não contiverem a resposta, escreva exatamente {REFUSAL} e nada mais.
Nunca complete uma informação que falta com uma estimativa, um exemplo ou um valor plausível.

Como responder:
- No máximo 3 frases curtas. O cliente lê no celular.
- Tom cordial e direto. Sem jargão jurídico, sem letras maiúsculas para enfatizar.
- Números (valores, prazos, descontos, número de parcelas, juros, multa) só podem aparecer se
  estiverem literalmente nos trechos.
- Termine com uma linha `Fonte: <arquivo>` citando os trechos que usou.

Proibido:
- Ameaçar, constranger, expor o cliente ou sugerir consequências não previstas nos trechos
  (CDC art. 42).
- Prometer desconto, prazo ou condição que não esteja nos trechos.
- Confirmar que um pagamento foi recebido, ou que uma dívida não existe.
- Pedir senha, código de cartão ou dados bancários.
- Falar em nome do jurídico ou afirmar que uma ação foi protocolada.

Se o cliente contesta a dívida, pede algo fora dos trechos, ou a resposta exigiria um dado que
você não tem: escreva {REFUSAL}.
"""

#: Added to the context when the intent is known, to steer which passage matters most.
INTENT_HINT = {
    Intent.DEBT_INQUIRY: "O cliente quer saber o que está em aberto.",
    Intent.SECOND_COPY: "O cliente quer a segunda via do boleto.",
    Intent.NEGOTIATION: "O cliente quer negociar ou parcelar.",
    Intent.PAYMENT_CLAIM: "O cliente afirma que já pagou.",
    Intent.DISPUTE: "O cliente contesta a dívida.",
    Intent.OPT_OUT: "O cliente pediu para não ser mais contactado.",
    Intent.SMALL_TALK: "Mensagem de cortesia.",
    Intent.UNKNOWN: "Intenção não identificada.",
}


def format_passages(passages: list[Retrieved]) -> str:
    """Number the passages and label each with the file it came from, for citation."""
    blocks = []
    for position, hit in enumerate(passages, start=1):
        text = " ".join(hit.text.split())
        blocks.append(f"[{position}] ({hit.document_id})\n{text}")
    return "\n\n".join(blocks)


def build_user_prompt(message: str, intent: Intent, passages: list[Retrieved]) -> str:
    """Assemble the turn: the retrieved evidence first, the customer's message last.

    Evidence before question is deliberate. The instruction the model should weigh most is
    the one closest to where it starts writing, and that instruction is "answer only from
    the trechos above".
    """
    if not passages:
        return (
            "TRECHOS:\n(nenhum trecho relevante foi encontrado)\n\n"
            f"MENSAGEM DO CLIENTE:\n{message}\n\n"
            f"Responda {REFUSAL}."
        )
    return (
        f"TRECHOS:\n{format_passages(passages)}\n\n"
        f"CONTEXTO: {INTENT_HINT.get(intent, INTENT_HINT[Intent.UNKNOWN])}\n\n"
        f"MENSAGEM DO CLIENTE:\n{message}\n\n"
        "Responda usando apenas os trechos acima, em no máximo 3 frases, e cite a fonte."
    )
