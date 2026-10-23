"""Writing the outbound message: a template arm and an LLM arm behind one seam.

The proactive agent decided *whether* and *through which channel* to make contact. This
decides *what it says*, and it is the half the schedule asks for in deliverable 14.

The two arms exist for the same reason the responder has two:

**`TemplateComposer` is the control, and it is the ceiling on compliance.** It fills slots
from the brief, so it cannot state a figure the brief does not carry, and its wording is
fixed, so it cannot threaten or shame. It should score a perfect compliance rate -- and if
it does not, the compliance checker is wrong, not the composer. That is exactly how the
two bugs in `faithfulness.py` were found.

**`LLMComposer` is the treatment, and the thing it is supposed to buy is variety.** A
collection operation that sends the same four sentences to every customer at every stage
reads as machinery, and the customer learns to ignore it. So the comparison is two-sided
on purpose: the template arm is the compliance ceiling and the *personalisation floor*,
and the interesting question is whether the LLM arm can buy distinctness without paying
for it in compliance.

Failure falls back to the template and is tagged `llm:fallback`, for the reason the
responder does it: an evaluation must not be able to report the control's numbers under
the treatment's name.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from collection.llm.client import LLMClient, LLMError, get_client
from collection.outreach.brief import Brief, brl

#: What every message must contain, whoever wrote it. See `compliance.py`.
OPT_OUT_TOKEN = "SAIR"
AUTOMATED_MARKER = "mensagem automática"

#: At or below this cap the short templates are used instead of the full ones.
SHORT_CHANNEL_CAP = 160


@dataclass
class OutboundMessage:
    text: str
    composer: str
    stage: str
    channel: str
    latency_ms: float = 0.0
    #: The facts the composer was allowed to use, carried along so the message can be
    #: checked against its own brief later, without rebuilding it.
    facts: dict[str, object] = field(default_factory=dict)


class Composer(ABC):
    name: str

    @abstractmethod
    def compose(self, brief: Brief) -> OutboundMessage:
        """Write the message this brief justifies."""


#: One template per delinquency stage. The escalation across them is in what is *offered*
#: -- a reminder, then instalments, then a down payment -- never in tone: a message that
#: gets firmer as the debt ages is how collection prose slides into pressure, and the CDC
#: does not grade harassment by days late.
TEMPLATES = {
    "pre_due": (
        "Olá{saudacao}, aqui é a {credor} ({automatica}). "
        "Sua fatura de {valor} vence em {vencimento}. "
        "Se precisar da segunda via, é só responder. "
        "Para não receber mais avisos, responda {sair}."
    ),
    "1-7": (
        "Olá{saudacao}, aqui é a {credor} ({automatica}). "
        "Consta em aberto o valor de {valor}, com {dias} dias de atraso. "
        "Podemos dividir em até {parcelas}x ou aplicar {desconto}% de desconto à vista. "
        "Responda esta mensagem para combinar, ou {sair} para não receber mais."
    ),
    "8-30": (
        "Olá{saudacao}, aqui é a {credor} ({automatica}). "
        "Seu débito de {valor} está com {dias} dias de atraso. "
        "Dá para parcelar em até {parcelas}x com entrada de {entrada}%, "
        "ou pagar à vista com {desconto}% de desconto. "
        "Responda para combinar, ou {sair} para não receber mais."
    ),
    "31-60": (
        "Olá{saudacao}, aqui é a {credor} ({automatica}). "
        "Seu débito de {valor} está com {dias} dias de atraso. "
        "Temos condição de parcelar em até {parcelas}x com entrada de {entrada}%, "
        "ou {desconto}% de desconto à vista. "
        "Responda para combinar, ou {sair} para não receber mais."
    ),
    "60+": (
        "Olá{saudacao}, aqui é a {credor} ({automatica}). "
        "Seu débito de {valor} está com {dias} dias de atraso e segue em aberto. "
        "Ainda dá para negociar: até {parcelas}x com entrada de {entrada}%, "
        "ou {desconto}% de desconto à vista. "
        "Responda para combinar, ou {sair} para não receber mais."
    ),
}


#: The same messages inside an SMS segment.
#:
#: These exist because the control arm failed its own length check: every full template
#: runs to roughly 250 characters, and an SMS segment is 160. A second segment is a second
#: message, billed and delivered separately, and a collection message that arrives split in
#: two reads as broken rather than as careful.
#:
#: What the cap costs is visible in the wording: the greeting by name goes first, because
#: the three mandatory disclosures -- who is writing, that it is automated, how to stop it
#: -- are not negotiable and consume most of the budget. So the message is a function of
#: `(stage, channel)` and not of stage alone, and on the narrow channel personalisation is
#: the first thing the cap takes.
SHORT_TEMPLATES = {
    "pre_due": (
        "{credor} ({automatica}): sua fatura de {valor} vence em {vencimento}. "
        "Responda para a segunda via, ou {sair} para não receber mais."
    ),
    "1-7": (
        "{credor} ({automatica}): {valor} em aberto há {dias} dias. "
        "Parcelamos em até {parcelas}x ou {desconto}% à vista. "
        "Responda para combinar, ou {sair} para sair."
    ),
    "8-30": (
        "{credor} ({automatica}): {valor} em aberto há {dias} dias. "
        "Até {parcelas}x com entrada de {entrada}%, ou {desconto}% à vista. "
        "Responda para combinar, ou {sair} para sair."
    ),
    "31-60": (
        "{credor} ({automatica}): {valor} em aberto há {dias} dias. "
        "Até {parcelas}x com entrada de {entrada}%, ou {desconto}% à vista. "
        "Responda para combinar, ou {sair} para sair."
    ),
    "60+": (
        "{credor} ({automatica}): {valor} em aberto há {dias} dias. "
        "Dá para negociar: até {parcelas}x ou {desconto}% à vista. "
        "Responda para combinar, ou {sair} para sair."
    ),
}


class TemplateComposer(Composer):
    """Slot filling. The control arm."""

    name = "template"

    def __init__(
        self,
        templates: dict[str, str] | None = None,
        short_templates: dict[str, str] | None = None,
    ) -> None:
        self.templates = templates or TEMPLATES
        self.short_templates = short_templates or SHORT_TEMPLATES

    def compose(self, brief: Brief) -> OutboundMessage:
        from collection.outreach.compliance import LENGTH_CAP

        terms = brief.terms
        narrow = LENGTH_CAP.get(brief.channel, LENGTH_CAP["chat"]) <= SHORT_CHANNEL_CAP
        library = self.short_templates if narrow else self.templates
        template = library.get(brief.stage, library["1-7"])
        text = template.format(
            saudacao=f" {brief.first_name}" if brief.first_name else "",
            credor=brief.creditor,
            automatica=AUTOMATED_MARKER,
            valor=brl(brief.amount),
            vencimento=brief.due_date.strftime("%d/%m/%Y") if brief.due_date else "breve",
            dias=brief.days_late,
            parcelas=brief.instalments,
            desconto=_plain(terms.discount_percent) if terms else 0,
            entrada=_plain(terms.entrada_percent) if terms else 0,
            sair=OPT_OUT_TOKEN,
        )
        return OutboundMessage(
            text=text,
            composer=self.name,
            stage=brief.stage,
            channel=brief.channel,
            facts=brief.facts(),
        )


def _plain(value: float) -> str:
    """`10.0` -> `10`, because `10.0% de desconto` is not how anyone writes it."""
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}".replace(".", ",")


SYSTEM_PROMPT = f"""\
Você escreve mensagens de cobrança para uma empresa brasileira, enviadas por WhatsApp ou SMS.

REGRA PRINCIPAL: use **somente** os dados do bloco FATOS. Qualquer número que apareça na sua
mensagem (valor, dias, parcelas, desconto, entrada, juros, data) precisa estar literalmente nos
FATOS. Nunca estime, arredonde nem invente um valor, um prazo ou uma condição.

A mensagem precisa obrigatoriamente:
- identificar o credor pelo nome que está nos FATOS;
- dizer que é uma {AUTOMATED_MARKER};
- terminar oferecendo a saída: responder {OPT_OUT_TOKEN} para não receber mais mensagens;
- caber em no máximo 4 frases curtas.

Proibido (CDC art. 42 e LGPD):
- ameaçar, constranger, expor o cliente ou mencionar cobrador, advogado, processo, protesto,
  negativação, serasa ou "último aviso";
- usar urgência artificial, letras maiúsculas para pressionar, ou emojis de alerta;
- afirmar consequências que não estão nos FATOS;
- pedir senha, código de cartão ou dados bancários;
- oferecer desconto ou número de parcelas acima do que está nos FATOS.

Tom: cordial, direto, adulto. O cliente está com uma dívida, não em falta moral.
Escreva apenas a mensagem, sem comentários e sem aspas.
"""


def build_user_prompt(brief: Brief) -> str:
    facts = "\n".join(f"- {key}: {value}" for key, value in brief.facts().items())
    return (
        f"FATOS:\n{facts}\n\n"
        "Escreva a mensagem de contato para este cliente, usando apenas os FATOS acima."
    )


class LLMComposer(Composer):
    """Writes the message with a model, under the same constraints the template encodes."""

    name = "llm"

    def __init__(
        self,
        client: LLMClient | None = None,
        fallback: Composer | None = None,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self.client = client or get_client()
        self.fallback = fallback if fallback is not None else TemplateComposer()
        self.system_prompt = system_prompt
        self.failures: list[str] = []

    def compose(self, brief: Brief) -> OutboundMessage:
        try:
            completion = self.client.complete(self.system_prompt, build_user_prompt(brief))
        except LLMError as error:
            self.failures.append(str(error))
            message = self.fallback.compose(brief)
            message.composer = f"{self.name}:fallback"
            return message

        text = completion.text.strip().strip('"')
        if not text:
            message = self.fallback.compose(brief)
            message.composer = f"{self.name}:fallback"
            return message

        return OutboundMessage(
            text=text,
            composer=self.name,
            stage=brief.stage,
            channel=brief.channel,
            latency_ms=completion.latency_ms,
            facts=brief.facts(),
        )


COMPOSERS: dict[str, type[Composer]] = {"template": TemplateComposer, "llm": LLMComposer}


def get_composer(name: str = "template") -> Composer:
    if name not in COMPOSERS:
        raise ValueError(f"Unknown composer: {name!r}. Available: {', '.join(COMPOSERS)}")
    return COMPOSERS[name]()
