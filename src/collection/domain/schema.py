"""Domain model of the collection cycle, plus the data dictionary.

The four tables below are the contract between the data source (real ERP or synthetic
generator) and everything downstream. The data dictionary required by deliverable 3 of
the schedule is generated from here, so documentation cannot drift from the code.

Per-field LGPD (Brazilian data protection law) classification:
  identifier  -> direct personal data, dropped or pseudonymized during ingestion
  quasi_id    -> can re-identify in combination, generalized during ingestion
  operational -> business data, kept as is
"""

from dataclasses import dataclass, field

Sensitivity = str  # "identifier" | "quasi_id" | "operational"


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    description: str
    sensitivity: Sensitivity = "operational"
    example: str = ""


@dataclass(frozen=True)
class Table:
    name: str
    description: str
    key: str
    fields: list[Field] = field(default_factory=list)

    @property
    def columns(self) -> list[str]:
        return [f.name for f in self.fields]

    def fields_with_sensitivity(self, s: Sensitivity) -> list[Field]:
        return [f for f in self.fields if f.sensitivity == s]


CUSTOMERS = Table(
    name="customers",
    description="Customer registry (individuals or companies) subject to collection.",
    key="customer_id",
    fields=[
        Field("customer_id", "str", "Customer identifier in the ERP.", "identifier", "C000123"),
        Field("name", "str", "Legal or full name.", "identifier", "Maria Silva"),
        Field("tax_id", "str", "Tax document (CPF/CNPJ).", "identifier", "123.456.789-00"),
        Field("email", "str", "Contact e-mail.", "identifier", "maria@example.com"),
        Field("phone", "str", "Phone/WhatsApp number.", "identifier", "+5547999999999"),
        Field("city", "str", "City of the main address.", "quasi_id", "Jaragua do Sul"),
        Field("state", "str", "Brazilian state (UF).", "quasi_id", "SC"),
        Field("signup_date", "date", "Date the account was opened.", "quasi_id", "2021-03-14"),
        Field(
            "segment", "str", "Commercial segment (retail, wholesale, services).", example="retail"
        ),
        Field("size_tier", "str", "Customer size (small, medium, large).", example="small"),
        Field("credit_limit", "float", "Granted credit limit, in BRL.", example="15000.00"),
        Field("preferred_channel", "str", "Declared contact channel.", example="whatsapp"),
        Field(
            "opt_out",
            "bool",
            "Customer asked not to be contacted (CDC art. 8 / LGPD).",
            example="False",
        ),
    ],
)

INVOICES = Table(
    name="invoices",
    description="Receivables (invoices/boletos). Unit of analysis of the predictive module.",
    key="invoice_id",
    fields=[
        Field("invoice_id", "str", "Receivable identifier.", "identifier", "D0000456"),
        Field("customer_id", "str", "Debtor.", "identifier", "C000123"),
        Field("document_number", "str", "Document number in the ERP.", "quasi_id", "NF-2026-0456"),
        Field("amount", "float", "Original amount, in BRL.", example="1250.90"),
        Field("issue_date", "date", "Issue date.", example="2026-01-10"),
        Field("due_date", "date", "Due date.", example="2026-02-10"),
        Field(
            "payment_date",
            "date",
            "Settlement date; empty while outstanding.",
            example="2026-02-27",
        ),
        Field(
            "days_late",
            "int",
            "Days between due date and payment (or today, if open).",
            example="17",
        ),
        Field(
            "status", "str", "open | paid | paid_late | defaulted | agreement.", example="paid_late"
        ),
        Field("payment_method", "str", "boleto, pix, card, transfer.", example="pix"),
    ],
)

COLLECTION_EVENTS = Table(
    name="collection_events",
    description="History of contact attempts and their outcomes.",
    key="event_id",
    fields=[
        Field("event_id", "str", "Event identifier.", "identifier", "E0001234"),
        Field("invoice_id", "str", "Receivable being collected.", "identifier", "D0000456"),
        Field("customer_id", "str", "Customer contacted.", "identifier", "C000123"),
        Field("event_time", "datetime", "Moment of contact.", example="2026-02-15 10:32:00"),
        Field("channel", "str", "whatsapp, phone, email, sms.", example="whatsapp"),
        Field("kind", "str", "reminder, collection, negotiation, notice.", example="collection"),
        Field("delinquency_stage", "str", "pre_due, 1-7, 8-30, 31-60, 60+.", example="8-30"),
        Field(
            "outcome",
            "str",
            "no_answer, replied, promised_to_pay, disputed, opt_out.",
            example="promised_to_pay",
        ),
        Field("contact_cost", "float", "Estimated cost of the contact, in BRL.", example="0.35"),
    ],
)

AGREEMENTS = Table(
    name="agreements",
    description="Installment agreements settled during collection.",
    key="agreement_id",
    fields=[
        Field("agreement_id", "str", "Agreement identifier.", "identifier", "A000078"),
        Field("customer_id", "str", "Customer bound by the agreement.", "identifier", "C000123"),
        Field("invoice_id", "str", "Renegotiated receivable.", "identifier", "D0000456"),
        Field("agreement_date", "date", "Date the agreement was settled.", example="2026-02-20"),
        Field("agreement_amount", "float", "Total negotiated amount.", example="1400.00"),
        Field("installments", "int", "Number of installments.", example="3"),
        Field("status", "str", "current, fulfilled, broken.", example="fulfilled"),
    ],
)

TABLES: list[Table] = [CUSTOMERS, INVOICES, COLLECTION_EVENTS, AGREEMENTS]
TABLES_BY_NAME: dict[str, Table] = {t.name: t for t in TABLES}

# Targets of the predictive module (block II of the schedule).
TARGET_TASK_1 = "target_payment_propensity"  # will an overdue receivable be settled?
TARGET_TASK_2 = "target_future_default"  # will a not-yet-due receivable default?
PROPENSITY_WINDOW_DAYS = 30
DEFAULT_THRESHOLD_DAYS = 60
