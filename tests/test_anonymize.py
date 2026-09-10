from collection.data.anonymize import DROPPED, anonymize, lgpd_report, pseudonym


def test_direct_personal_data_is_removed(dataset):
    for df in anonymize(dataset).values():
        assert not (DROPPED & set(df.columns))


def test_pseudonymization_preserves_relationships_across_tables(dataset):
    tables = anonymize(dataset)
    customers = set(tables["customers"]["customer_id"])
    assert set(tables["invoices"]["customer_id"]) <= customers
    assert tables["customers"]["customer_id"].is_unique


def test_pseudonym_is_deterministic_and_hides_the_original():
    assert pseudonym("C000001") == pseudonym("C000001")
    assert pseudonym("C000001") != pseudonym("C000002")
    assert "C000001" not in pseudonym("C000001")


def test_lgpd_report_classifies_every_field():
    report = lgpd_report()
    assert report["treatment"].notna().all()
    assert set(report["treatment"].unique()) >= {"dropped", "kept"}
