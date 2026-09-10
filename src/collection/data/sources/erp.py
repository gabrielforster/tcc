"""Extraction from the production ERP.

Not enabled yet: it depends on access being granted (risk 1 in the schedule). The queries
are already written so that switching over is only a matter of filling in ERP_DATABASE_URL
and adjusting the target ERP's table names.
"""

import pandas as pd
from sqlalchemy import create_engine, text

from collection.config import settings
from collection.data.sources.base import DataSource, RawDataset

QUERIES: dict[str, str] = {
    "customers": """
        SELECT id            AS customer_id,
               nome          AS name,
               cpf_cnpj      AS tax_id,
               email, telefone AS phone, cidade AS city, uf AS state,
               data_cadastro AS signup_date,
               segmento      AS segment,
               porte         AS size_tier,
               limite_credito AS credit_limit,
               canal_preferencial AS preferred_channel,
               opt_out
        FROM   clientes
    """,
    "invoices": """
        SELECT id            AS invoice_id,
               cliente_id    AS customer_id,
               numero        AS document_number,
               valor         AS amount,
               data_emissao  AS issue_date,
               data_vencimento AS due_date,
               data_pagamento  AS payment_date,
               status,
               forma_pagamento AS payment_method
        FROM   contas_receber
        WHERE  data_emissao >= :since
    """,
    "collection_events": """
        SELECT id            AS event_id,
               documento_id  AS invoice_id,
               cliente_id    AS customer_id,
               data_evento   AS event_time,
               canal         AS channel,
               tipo          AS kind,
               resultado     AS outcome,
               custo_contato AS contact_cost
        FROM   cobranca_eventos
        WHERE  data_evento >= :since
    """,
    "agreements": """
        SELECT id            AS agreement_id,
               cliente_id    AS customer_id,
               documento_id  AS invoice_id,
               data_acordo   AS agreement_date,
               valor_acordo  AS agreement_amount,
               parcelas      AS installments,
               status
        FROM   acordos
        WHERE  data_acordo >= :since
    """,
}


class ERPSource(DataSource):
    name = "erp"

    def __init__(self, since: str = "2023-01-01") -> None:
        self.since = since

    def extract(self) -> RawDataset:
        if not settings.erp_database_url:
            raise RuntimeError(
                "ERP_DATABASE_URL is not configured. While ERP access is pending, "
                "use DATA_SOURCE=synthetic."
            )
        engine = create_engine(settings.erp_database_url)
        with engine.connect() as conn:
            tables = {
                name: pd.read_sql(text(sql), conn, params={"since": self.since})
                for name, sql in QUERIES.items()
            }
        return RawDataset(
            customers=tables["customers"],
            invoices=tables["invoices"],
            events=tables["collection_events"],
            agreements=tables["agreements"],
        )
