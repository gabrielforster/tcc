import pandas as pd

from collection.data.sources.synthetic import SyntheticSource, delinquency_stage


def test_synthetic_dataset_matches_the_schema(dataset):
    assert dataset.validate() == []


def test_generation_is_deterministic_for_the_same_seed():
    a = SyntheticSource(n_customers=40, n_invoices=300, seed=1).extract()
    b = SyntheticSource(n_customers=40, n_invoices=300, seed=1).extract()
    pd.testing.assert_frame_equal(a.invoices, b.invoices)


def test_receivables_paid_on_time_generate_no_collection_events(dataset):
    paid_on_time = dataset.invoices.loc[dataset.invoices["status"] == "paid", "invoice_id"]
    assert not dataset.events["invoice_id"].isin(paid_on_time).any()


def test_no_contact_is_recorded_after_the_cutoff_date(dataset):
    cutoff = pd.Timestamp(dataset.invoices["payment_date"].dropna().max())
    assert (pd.to_datetime(dataset.events["event_time"]) <= cutoff + pd.Timedelta(days=1)).all()


def test_delinquency_stages_cover_the_template_buckets():
    assert [delinquency_stage(d) for d in (0, 3, 15, 45, 90)] == [
        "pre_due",
        "1-7",
        "8-30",
        "31-60",
        "60+",
    ]
