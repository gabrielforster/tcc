"""Checking an outbound message before it is sent, and scoring it afterwards.

The responsive agent has `faithfulness.py`: did the answer invent anything the passages
did not support. Outbound needs the same question asked of the brief, plus a second one
inbound does not face -- *was it allowed to say that at all*. A customer who writes to us
chose to; a customer we write to did not, and the CDC regulates the second much harder
than the first.

Five checks, each failing for a different reason:

**Invented figures.** Every number in the message must appear in the brief. In this domain
a digit is almost always a claim about somebody's debt, so the rule is strict with no
tolerance band: the template arm proves the standard is satisfiable, and anything an LLM
adds beyond it is an invention about money.

**Threats and shaming.** CDC art. 42 forbids exposing the debtor to ridicule or subjecting
them to any kind of constraint. The lexicon below is deliberately *conservative*: it flags
vocabulary that cannot appear in a compliant first contact (`advogado`, `protesto`,
`vergonha`, `último aviso`), not merely firm wording. It is a floor, not a certificate --
a message can clear every term here and still read as pressure, which is why the generated
report prints the messages in full.

**Missing disclosures.** The creditor must be named, the message must say it is automated,
and it must carry the opt-out -- the LGPD right to object is worth nothing if the customer
is not told it exists.

**Over-authority offers.** A number can be legitimate as a *fact* and illegitimate as an
*offer*: `juros de 1,5% ao mês` is in the brief, `1,5% de desconto` would be too, and
`40% de desconto` at a stage whose table row says 20 is a promise the operation cannot
honour. So discounts and instalment counts are parsed where they are offered and compared
against the stage's own limits, independently of whether the digits appear in the brief.

**Length.** A message the customer has to expand to read is a message they do not read.
The cap is per channel, because 160 characters is the SMS segment and WhatsApp is not SMS.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from collection.llm.faithfulness import normalise_number, numbers_in
from collection.outreach.brief import Brief
from collection.outreach.compose import AUTOMATED_MARKER, OPT_OUT_TOKEN, OutboundMessage

#: Vocabulary that cannot appear in a compliant collection message. Accent-folded before
#: matching, so `negativacao` and `negativação` are the same word.
FORBIDDEN_TERMS = (
    "advogado",
    "juridico",
    "processo",
    "acao judicial",
    "execucao",
    "protesto",
    "cartorio",
    "serasa",
    "spc",
    "negativar",
    "negativacao",
    "nome sujo",
    "vergonha",
    "caloteiro",
    "devedor contumaz",
    "ultimo aviso",
    "ultima chance",
    "consequencias",
    "medidas cabiveis",
    "oficial de justica",
    "penhora",
    "bloqueio",
    "cobrador",
    "vamos ate voce",
    "sua familia",
    "seu chefe",
    "seu trabalho",
)

#: Longest message that still reads as one bubble, by channel.
LENGTH_CAP = {"whatsapp": 480, "sms": 160, "phone": 480, "email": 1200}

_DISCOUNT = re.compile(r"(\d[\d.,]*)\s*%\s*(?:de\s+)?desconto", re.IGNORECASE)
_INSTALMENTS = re.compile(r"(?:em\s+até\s+|em\s+|até\s+)?(\d{1,3})\s*(?:x|vezes|parcelas)", re.I)


def fold(text: str) -> str:
    """Lowercase and strip accents, so a lexicon written once matches either spelling."""
    stripped = unicodedata.normalize("NFD", text.lower())
    return "".join(char for char in stripped if unicodedata.category(char) != "Mn")


def allowed_numbers(brief: Brief) -> set[str]:
    """Every figure the brief licenses, in canonical form.

    A date contributes its parts as well as itself: `15/11/2026` reaches the message as
    three numbers, and flagging the day of the month as an invented figure would be
    nonsense.
    """
    allowed: set[str] = set()
    for value in brief.facts().values():
        allowed.update(numbers_in(str(value)))
    if brief.due_date is not None:
        allowed.update(
            {
                normalise_number(str(brief.due_date.day)),
                normalise_number(str(brief.due_date.month)),
                normalise_number(str(brief.due_date.year)),
            }
        )
    # The amount rounded to whole reais: "R$ 1.250,00" is often written "R$ 1.250".
    allowed.add(normalise_number(f"{brief.amount:.2f}"))
    allowed.add(normalise_number(str(int(brief.amount))))
    return {number for number in allowed if number}


@dataclass
class Compliance:
    """What a message got wrong, in the terms somebody would have to answer for."""

    invented_numbers: list[str] = field(default_factory=list)
    forbidden_terms: list[str] = field(default_factory=list)
    missing_disclosures: list[str] = field(default_factory=list)
    over_authority: list[str] = field(default_factory=list)
    length: int = 0
    length_cap: int = 0

    @property
    def too_long(self) -> bool:
        return self.length > self.length_cap

    @property
    def violations(self) -> list[str]:
        found = [f"invented:{number}" for number in self.invented_numbers]
        found += [f"forbidden:{term}" for term in self.forbidden_terms]
        found += [f"missing:{item}" for item in self.missing_disclosures]
        found += [f"authority:{item}" for item in self.over_authority]
        if self.too_long:
            found.append(f"length:{self.length}>{self.length_cap}")
        return found

    @property
    def compliant(self) -> bool:
        return not self.violations

    @property
    def sendable(self) -> bool:
        """Whether the message may go out as written.

        Length alone does not block it -- a long message is bad, not unlawful -- so this
        is deliberately narrower than `compliant`, which is what the report scores.
        """
        return not (
            self.invented_numbers
            or self.forbidden_terms
            or self.missing_disclosures
            or self.over_authority
        )

    def as_row(self) -> dict[str, object]:
        return {
            "compliant": self.compliant,
            "invented_numbers": len(self.invented_numbers),
            "forbidden_terms": len(self.forbidden_terms),
            "missing_disclosures": len(self.missing_disclosures),
            "over_authority": len(self.over_authority),
            "chars": self.length,
        }


def check(message: OutboundMessage | str, brief: Brief) -> Compliance:
    """Score one message against the brief it was written from."""
    text = message.text if isinstance(message, OutboundMessage) else message
    channel = message.channel if isinstance(message, OutboundMessage) else brief.channel
    folded = fold(text)
    allowed = allowed_numbers(brief)

    invented = [number for number in numbers_in(text) if number not in allowed]
    forbidden = [term for term in FORBIDDEN_TERMS if term in folded]

    missing = []
    if brief.creditor and fold(brief.creditor) not in folded:
        missing.append("creditor")
    if fold(AUTOMATED_MARKER) not in folded:
        missing.append("automated")
    if OPT_OUT_TOKEN.lower() not in text.lower():
        missing.append("opt_out")

    return Compliance(
        invented_numbers=sorted(set(invented)),
        forbidden_terms=forbidden,
        missing_disclosures=missing,
        over_authority=_over_authority(text, brief),
        length=len(text),
        length_cap=LENGTH_CAP.get(channel, LENGTH_CAP["whatsapp"]),
    )


def _over_authority(text: str, brief: Brief) -> list[str]:
    """Offers beyond what this delinquency stage permits."""
    terms = brief.terms
    found: list[str] = []

    max_discount = terms.discount_percent if terms else 0.0
    for match in _DISCOUNT.finditer(text):
        offered = float(normalise_number(match.group(1)) or 0)
        if offered > max_discount:
            found.append(f"discount {offered:g}% > {max_discount:g}%")

    max_instalments = brief.instalments
    for match in _INSTALMENTS.finditer(text):
        offered = int(match.group(1))
        if offered > max_instalments:
            found.append(f"instalments {offered} > {max_instalments}")

    return found
