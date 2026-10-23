import pytest

from collection.config import settings
from collection.data.sources.synthetic import SyntheticSource
from collection.rag.documents import load_directory
from collection.rag.index import VectorIndex


@pytest.fixture(scope="session")
def dataset():
    return SyntheticSource(n_customers=120, n_invoices=1500, seed=7).extract()


@pytest.fixture(scope="session")
def index():
    return VectorIndex().add_documents(load_directory(settings.dir_knowledge)).build()
