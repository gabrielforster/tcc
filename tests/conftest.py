import os

# Cap every thread pool before numpy, torch or the boosters load: each one otherwise
# starts a thread per core, and two suites running side by side saturate the machine.
# Raise it per run with COLLECTION_TEST_THREADS.
_THREADS = os.environ.get("COLLECTION_TEST_THREADS", "2")
for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_var, _THREADS)
os.environ.setdefault("N_JOBS", _THREADS)

import pytest  # noqa: E402

from collection.config import settings  # noqa: E402
from collection.data.sources.synthetic import SyntheticSource  # noqa: E402
from collection.rag.documents import load_directory  # noqa: E402
from collection.rag.index import VectorIndex  # noqa: E402


@pytest.fixture(scope="session")
def dataset():
    return SyntheticSource(n_customers=120, n_invoices=1500, seed=7).extract()


@pytest.fixture(scope="session")
def index():
    return VectorIndex().add_documents(load_directory(settings.dir_knowledge)).build()
