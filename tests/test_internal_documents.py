"""No serving path may retrieve an internal document, the in-memory ones included.

The pgvector retriever filters on the audience in SQL (tests/test_seed_tenant.py). The
call server and the chat with no tenant behind it build an in-memory index from
`knowledge/` instead, and that index has no notion of an audience: it has to be given
only what a customer may be shown, or an anonymous caller can be read the discount
ceilings.
"""

from collection.api import call as call_api
from collection.api.chat import ChatService
from collection.config import settings
from collection.rag.documents import INTERNAL_DOCUMENTS, load_customer_facing, load_directory

#: Questions a customer could ask whose best answer is an internal document.
PROBES = (
    "qual o desconto maximo que posso conceder?",
    "como comecar a conversa de cobranca por telefone?",
    "o cliente disse que esta desempregado, o que ofereco?",
)


def _retrieved(retriever) -> set[str]:
    return {hit.document_id for probe in PROBES for hit in retriever.search(probe, top_k=10)}


def test_the_customer_facing_corpus_holds_everything_but_the_internal_documents():
    everything = {document.root_id for document in load_directory(settings.dir_knowledge)}
    facing = {document.root_id for document in load_customer_facing(settings.dir_knowledge)}

    assert everything >= INTERNAL_DOCUMENTS, "an internal document is missing from knowledge/"
    assert facing == everything - INTERNAL_DOCUMENTS


class _NoWhisper:
    """Stands in for Whisper: the call server loads it eagerly, and it is beside the point."""

    name = "no-whisper"

    def __init__(self, model: str = "small") -> None:
        self.model = model

    def _load(self) -> None:
        pass


def test_the_call_server_never_retrieves_an_internal_document(monkeypatch):
    monkeypatch.setattr(call_api, "WhisperTranscriber", _NoWhisper)

    channel = call_api.build_channel()

    retrieved = _retrieved(channel._core.retriever)
    assert retrieved, "the probes retrieved nothing, so the test proves nothing"
    assert not retrieved & INTERNAL_DOCUMENTS


def test_the_chat_without_a_tenant_never_retrieves_an_internal_document():
    channel = ChatService()._build("")

    retrieved = _retrieved(channel.retriever)
    assert retrieved, "the probes retrieved nothing, so the test proves nothing"
    assert not retrieved & INTERNAL_DOCUMENTS
