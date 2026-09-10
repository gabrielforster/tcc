import pytest

from collection.data.sources.synthetic import SyntheticSource


@pytest.fixture(scope="session")
def dataset():
    return SyntheticSource(n_customers=120, n_invoices=1500, seed=7).extract()
