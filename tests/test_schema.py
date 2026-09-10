from collection.domain.schema import TABLES


def test_every_table_declares_its_key_among_the_fields():
    for table in TABLES:
        assert table.key in table.columns, table.name


def test_field_names_are_unique_per_table():
    for table in TABLES:
        assert len(table.columns) == len(set(table.columns)), table.name
