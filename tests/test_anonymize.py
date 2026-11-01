import uuid

from collection.data.anonymize import DROPPED, anonymize, lgpd_report, pseudonym

TENANT = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER = uuid.UUID("22222222-2222-2222-2222-222222222222")


def test_direct_personal_data_is_removed(dataset):
    for df in anonymize(dataset, tenant=TENANT).values():
        assert not (DROPPED & set(df.columns))


def test_pseudonymization_preserves_relationships_across_tables(dataset):
    tables = anonymize(dataset, tenant=TENANT)
    customers = set(tables["customers"]["customer_id"])
    assert set(tables["invoices"]["customer_id"]) <= customers
    assert tables["customers"]["customer_id"].is_unique


def test_pseudonym_is_deterministic_and_hides_the_original():
    assert pseudonym("C000001", tenant=TENANT) == pseudonym("C000001", tenant=TENANT)
    assert pseudonym("C000001", tenant=TENANT) != pseudonym("C000002", tenant=TENANT)
    assert "C000001" not in pseudonym("C000001", tenant=TENANT)


def test_the_same_customer_is_a_different_pseudonym_in_another_tenant():
    """Otherwise the pseudonym is a key that joins one tenant's rows to another's."""
    assert pseudonym("C000001", tenant=TENANT) != pseudonym("C000001", tenant=OTHER)


def test_two_tenants_anonymizing_the_same_source_share_no_key(dataset):
    mine = anonymize(dataset, tenant=TENANT)
    theirs = anonymize(dataset, tenant=OTHER)
    for table in mine:
        for key in {"customer_id", "invoice_id"} & set(mine[table].columns):
            assert not set(mine[table][key]) & set(theirs[table][key])


def test_lgpd_report_classifies_every_field():
    report = lgpd_report()
    assert report["treatment"].notna().all()
    assert set(report["treatment"].unique()) >= {"dropped", "kept"}
