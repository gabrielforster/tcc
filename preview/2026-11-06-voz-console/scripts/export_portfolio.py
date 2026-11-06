"""Export what the two published models say about every customer, for the console mock.

Read-only: runs the same path as `collection score` (features from the tenant's interim
tables, the latest platform model) for both tasks, adds per-customer SHAP reasons, and
writes one JSON the mock pages load. Nothing is written to the database.

usage (repo root): COLLECTION_TENANT=desenvolvimento uv run python -I \
    preview/2026-11-06-voz-console/scripts/export_portfolio.py preview/2026-11-06-voz-console/mock/portfolio.json
"""

import json
import sys

import numpy as np
import pandas as pd
import shap
from sqlalchemy import select

from collection.data.ingest import load_interim
from collection.db.models import Tenant
from collection.db.session import unit_of_work
from collection.db.repository import TenantScope
from collection.features.build import build_features
from collection.models.store import latest_model, read_bundle

LABELS = {
    "hist_avg_days_late": "atraso médio histórico",
    "hist_recent_days_late": "atraso nas últimas faturas",
    "hist_late_rate": "proporção de faturas pagas com atraso",
    "hist_max_days_late": "maior atraso já registrado",
    "hist_default_rate": "histórico de inadimplência",
    "hist_invoice_count": "tempo de relacionamento (nº de faturas)",
    "hist_response_rate": "taxa de resposta a contatos",
    "hist_contact_count": "contatos de cobrança recebidos",
    "hist_contact_cost": "custo de cobrança acumulado",
    "hist_days_late_trend": "tendência do atraso",
    "hist_days_since_prev_invoice": "intervalo entre faturas",
    "hist_avg_amount": "valor médio das faturas",
    "amount_to_limit_ratio": "valor da fatura vs. limite de crédito",
    "amount": "valor da fatura",
    "credit_limit": "limite de crédito",
    "months_since_signup": "meses de cliente",
    "term_days": "prazo da fatura",
}


def label(encoded: str) -> str:
    name = encoded.split("__", 1)[-1]
    if name in LABELS:
        return LABELS[name]
    for raw in ("segment", "size_tier", "state", "preferred_channel", "payment_method",
                "due_month", "due_weekday", "due_quarter", "opt_out"):
        if name.startswith(raw + "_"):
            return f"{raw.replace('_', ' ')} = {name[len(raw) + 1:]}"
    return name.replace("_", " ")


def score(scope: TenantScope, task: str, tables) -> tuple[pd.DataFrame, dict]:
    model = latest_model(scope.session, task)
    bundle = read_bundle(model)
    pipeline = bundle["pipeline"]
    features = build_features(task, tables)
    x = features[bundle["features"]]
    proba = pipeline.predict_proba(x)[:, 1]
    transformed = pipeline.named_steps["preprocess"].transform(x)
    names = list(pipeline.named_steps["preprocess"].get_feature_names_out())
    values = np.asarray(shap.TreeExplainer(pipeline.named_steps["model"]).shap_values(transformed))
    frame = features[["customer_id", "invoice_id", "reference_date", "amount"]].copy()
    frame["p"] = proba
    meta = {"threshold": float(bundle.get("threshold", 0.5)), "champion": model.name if hasattr(model, "name") else task,
            "version": getattr(model, "version", None)}
    return frame.reset_index(drop=True), {"meta": meta, "shap": values, "names": names,
                                          "features": features.reset_index(drop=True)}


def reasons(rows: np.ndarray, shap_values: np.ndarray, names: list[str], k: int = 4):
    mean = shap_values[rows].mean(axis=0)
    order = np.argsort(-np.abs(mean))[:k]
    return [{"feature": label(names[i]), "impact": round(float(mean[i]), 4)} for i in order]


def main(out: str) -> None:
    import os
    slug = os.environ.get("COLLECTION_TENANT", "desenvolvimento")
    with unit_of_work(platform=True) as lookup:
        tenant_id = lookup.scalar(select(Tenant.id).where(Tenant.slug == slug))
    with unit_of_work(tenant_id) as session:
        scope = TenantScope(session, tenant_id)
        tables = load_interim(scope)
        prop, P = score(scope, "propensity", tables)
        dflt, D = score(scope, "default", tables)

    customers = tables["customers"].set_index("customer_id")
    invoices = tables["invoices"].copy()
    invoices["payment_date"] = pd.to_datetime(invoices["payment_date"])
    invoices["due_date"] = pd.to_datetime(invoices["due_date"])
    as_of = invoices["payment_date"].max()
    open_inv = invoices[invoices["payment_date"].isna()]
    overdue = (as_of - open_inv.groupby("customer_id")["due_date"].min()).dt.days.clip(lower=0)
    result = []
    for cid in sorted(set(prop.customer_id) & set(dflt.customer_id)):
        prow = np.flatnonzero(prop.customer_id.values == cid)
        drow = np.flatnonzero(dflt.customer_id.values == cid)
        feats = P["features"].iloc[prow]
        c = customers.loc[cid] if cid in customers.index else pd.Series(dtype=object)
        inv = invoices[invoices.customer_id == cid]
        result.append({
            "id": str(cid),
            "segment": str(feats["segment"].iloc[-1]), "size_tier": str(feats["size_tier"].iloc[-1]),
            "state": str(feats["state"].iloc[-1]), "channel": str(feats["preferred_channel"].iloc[-1]),
            "credit_limit": float(feats["credit_limit"].iloc[-1]),
            "invoices": int(len(inv)),
            "open_amount": float(inv.loc[inv.payment_date.isna(), "amount"].sum()) if "payment_date" in inv else 0.0,
            "avg_days_late": float(feats["hist_avg_days_late"].iloc[-1]) if "hist_avg_days_late" in feats else None,
            "late_rate": float(feats["hist_late_rate"].iloc[-1]) if "hist_late_rate" in feats else None,
            "days_overdue": int(overdue.get(cid, 0)),
            "p_pay": float(prop.p.values[prow].mean()),
            "p_default": float(dflt.p.values[drow].mean()),
            "why_pay": reasons(prow, P["shap"], P["names"]),
            "why_default": reasons(drow, D["shap"], D["names"]),
        })

    payload = {"as_of": str(as_of.date()), "propensity": P["meta"], "default": D["meta"], "customers": result}
    with open(out, "w") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, default=str)
    df = pd.DataFrame(result)
    print(len(df), "customers")
    print(df[["p_pay", "p_default"]].describe().round(3))
    print(P["meta"], D["meta"])


if __name__ == "__main__":
    main(sys.argv[1])
