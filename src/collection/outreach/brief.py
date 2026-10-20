"""What the system knows about a receivable before it writes to the customer.

A `Brief` is the *only* input a composer gets. That is the whole point of the type: an
outbound message may state a figure exactly when the brief carries it, which is what makes
`compliance.py` able to tell a legitimate amount from an invented one. It is the outbound
twin of the retrieved passages the responsive agent answers from -- same discipline, same
reason.

The commercial terms are not written here. They are read from
`knowledge/tabela-de-parcelamento.csv`, the same file the retriever serves to the
responsive agent, so the offer the system *sends* and the answer it *gives* cannot
disagree. Duplicating the table into a Python dict would let them drift silently, and the
first symptom would be a customer quoting an outbound message back at the chat.
"""

import csv
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

from collection.config import settings
from collection.data.sources.synthetic import delinquency_stage


def brl(amount: float) -> str:
    """`1250.0` -> `R$ 1.250,00`, in the notation a Brazilian customer reads."""
    return "R$ " + f"{amount:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


#: Delinquency stage -> the row of the instalment table that applies to it.
#:
#: `pre_due` has no row: nothing is late yet, so there is nothing to discount and no
#: instalment plan to offer -- the message is a reminder, not an offer. The table's
#: `acima de 180 dias` row is likewise unreachable from a stage, because `60+` is the last
#: bucket the detector produces; a longer-horizon stage would need a bucket of its own.
STAGE_TO_FAIXA = {
    "1-7": "1 a 7 dias",
    "8-30": "8 a 30 dias",
    "31-60": "31 a 60 dias",
    "60+": "61 a 180 dias",
}

#: Legal floor on any instalment, from `knowledge/limites-de-alcada.md`.
MIN_INSTALMENT_BRL = 50.0


@dataclass(frozen=True)
class Terms:
    """The commercial offer available at one delinquency stage."""

    faixa: str
    entrada_percent: float
    max_instalments: int
    discount_percent: float
    interest_percent: float

    def instalments_for(self, amount: float) -> int:
        """How many instalments this amount actually supports.

        The table caps the count; the R$ 50,00 floor caps it again for a small debt, and
        the floor is the binding one more often than the table is. Offering twelve
        instalments of R$ 18,00 would be an offer the operation cannot honour.
        """
        affordable = int(amount // MIN_INSTALMENT_BRL)
        return max(1, min(self.max_instalments, affordable))


@lru_cache(maxsize=1)
def load_terms(path: Path | None = None) -> dict[str, Terms]:
    """Read the instalment table and key it by delinquency stage."""
    path = path or settings.dir_knowledge / "tabela-de-parcelamento.csv"
    by_faixa: dict[str, Terms] = {}
    with Path(path).open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            by_faixa[row["faixa_de_atraso"]] = Terms(
                faixa=row["faixa_de_atraso"],
                entrada_percent=float(row["entrada_minima_percentual"]),
                max_instalments=int(row["parcelas_maximas"]),
                discount_percent=float(row["desconto_a_vista_percentual"]),
                interest_percent=float(row["juros_ao_mes_percentual"]),
            )
    return {stage: by_faixa[faixa] for stage, faixa in STAGE_TO_FAIXA.items() if faixa in by_faixa}


@dataclass(frozen=True)
class Brief:
    """Everything a composer may use, and nothing else."""

    customer_id: str
    amount: float
    days_late: int
    customer_name: str = ""
    invoice_id: str | None = None
    due_date: date | None = None
    attempt: int = 1
    channel: str = "whatsapp"
    #: Who is collecting. Named in every message: an unidentified collector is the first
    #: thing the CDC's transparency duty rules out.
    creditor: str = "Simplifica"

    @property
    def stage(self) -> str:
        return delinquency_stage(self.days_late)

    @property
    def terms(self) -> Terms | None:
        return load_terms().get(self.stage)

    @property
    def first_name(self) -> str:
        return self.customer_name.split()[0] if self.customer_name else ""

    @property
    def instalments(self) -> int:
        terms = self.terms
        return terms.instalments_for(self.amount) if terms else 0

    @property
    def discount_percent(self) -> float:
        terms = self.terms
        return terms.discount_percent if terms else 0.0

    def facts(self) -> dict[str, object]:
        """The brief as the flat block of facts handed to a model."""
        terms = self.terms
        facts: dict[str, object] = {
            "credor": self.creditor,
            "nome": self.first_name or "(desconhecido)",
            "valor": brl(self.amount),
            "dias_de_atraso": self.days_late,
            "faixa": terms.faixa if terms else "a vencer",
            "tentativa": self.attempt,
            "canal": self.channel,
        }
        if self.due_date is not None:
            facts["vencimento"] = self.due_date.strftime("%d/%m/%Y")
        if terms is not None:
            facts["parcelas_maximas"] = self.instalments
            facts["desconto_a_vista_percentual"] = terms.discount_percent
            facts["entrada_minima_percentual"] = terms.entrada_percent
            facts["juros_ao_mes_percentual"] = terms.interest_percent
        return facts


def load_briefs(path: Path | None = None) -> list[Brief]:
    """Read `knowledge/outreach-briefs.json`."""
    import json

    path = Path(path or settings.dir_knowledge / "outreach-briefs.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    briefs = []
    for item in raw:
        due = item.get("due_date")
        briefs.append(
            Brief(
                customer_id=item["customer_id"],
                customer_name=item.get("customer_name", ""),
                amount=float(item["amount"]),
                days_late=int(item["days_late"]),
                invoice_id=item.get("invoice_id"),
                due_date=date.fromisoformat(due) if due else None,
                attempt=int(item.get("attempt", 1)),
                channel=item.get("channel", "whatsapp"),
            )
        )
    return briefs
