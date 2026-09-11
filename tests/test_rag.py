"""RAG pipeline: chunking, loading, indexing and retrieval evaluation.

Retrieval is tested separately from generation on purpose. An agent that invents an answer
because nothing relevant was retrieved has a retrieval failure, not a generation one, and
keeping the two measurable apart is what makes the eventual hallucination numbers mean
anything.
"""

import json

import pytest

from collection.rag.chunking import chunk_text, split_paragraphs
from collection.rag.documents import load_directory, load_file
from collection.rag.embeddings import TfidfEmbedder, get_embedder
from collection.rag.evaluate import Question, evaluate, load_questions, sweep
from collection.rag.index import VectorIndex

LONG_PARAGRAPH = " ".join(f"palavra{i}" for i in range(1200))


@pytest.fixture
def corpus(tmp_path):
    (tmp_path / "politica.md").write_text(
        "# Politica\n\n"
        "O contato de cobranca ocorre de segunda a sexta das 8h as 20h.\n\n"
        "Nao ha contato aos domingos nem feriados.\n",
        encoding="utf-8",
    )
    (tmp_path / "faq.md").write_text(
        "# FAQ\n\nA segunda via do boleto pode ser solicitada no atendimento.\n\n"
        "Aceitamos boleto, Pix e cartao de credito.\n",
        encoding="utf-8",
    )
    (tmp_path / "tabela.csv").write_text(
        "faixa,parcelas\n1 a 7 dias,3\n8 a 30 dias,6\n31 a 60 dias,10\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def index(corpus):
    return VectorIndex().add_documents(load_directory(corpus)).build()


# ----------------------------------------------------------------------- chunking
def test_a_short_document_stays_a_single_chunk():
    chunks = chunk_text("Um paragrafo curto.\n\nE outro.", document_id="d")
    assert len(chunks) == 1
    assert chunks[0].chunk_id == "d#0"


def test_an_oversized_document_is_split_within_the_configured_size():
    chunks = chunk_text(LONG_PARAGRAPH, document_id="d", max_words=400, overlap=0)
    assert len(chunks) > 1
    assert all(chunk.word_count <= 400 for chunk in chunks)


def test_text_with_no_sentence_boundaries_is_still_split():
    """Extracted PDF text often arrives as one unpunctuated run; it must not pass through
    whole and blow the model's context budget."""
    unpunctuated = " ".join(f"token{i}" for i in range(900))
    chunks = chunk_text(unpunctuated, document_id="d", max_words=200, overlap=0)
    assert len(chunks) >= 5
    assert max(chunk.word_count for chunk in chunks) <= 200


def test_consecutive_chunks_overlap_so_context_is_not_cut_at_the_seam():
    text = "\n\n".join(f"Paragrafo numero {i} com algum conteudo." for i in range(40))
    chunks = chunk_text(text, document_id="d", max_words=30, overlap=12)
    assert len(chunks) > 2
    first_tail = set(chunks[0].text.split())
    assert first_tail & set(chunks[1].text.split())


def test_chunking_keeps_paragraphs_whole_where_they_fit():
    """A chunk ending mid-sentence retrieves badly and reads worse when quoted back."""
    text = "Primeiro paragrafo completo.\n\nSegundo paragrafo completo."
    chunks = chunk_text(text, document_id="d", max_words=100)
    assert split_paragraphs(chunks[0].text) == split_paragraphs(text)


def test_metadata_travels_with_every_chunk():
    chunks = chunk_text(LONG_PARAGRAPH, document_id="d", metadata={"format": "md"}, max_words=200)
    assert all(chunk.metadata["format"] == "md" for chunk in chunks)


# ------------------------------------------------------------------------ loading
def test_a_directory_loads_every_supported_format(corpus):
    documents = load_directory(corpus)
    formats = {doc.metadata["format"] for doc in documents}
    assert {"md", "csv"} <= formats


def test_each_csv_row_becomes_its_own_retrievable_document(corpus):
    documents = [d for d in load_directory(corpus) if d.metadata["format"] == "csv"]
    assert len(documents) == 3
    # Column names are kept in the text so a row reads standalone.
    assert "parcelas" in documents[0].text


def test_csv_rows_report_the_file_they_came_from(corpus):
    """A customer is cited a document, not a row number."""
    rows = [d for d in load_directory(corpus) if d.metadata["format"] == "csv"]
    assert all(row.root_id == "tabela.csv" for row in rows)
    assert rows[0].document_id != rows[1].document_id


def test_a_missing_knowledge_directory_is_reported_clearly(tmp_path):
    with pytest.raises(FileNotFoundError, match="Knowledge base"):
        load_directory(tmp_path / "nope")


def test_an_unsupported_file_type_is_rejected(tmp_path):
    path = tmp_path / "notes.xyz"
    path.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported"):
        load_file(path)


# ---------------------------------------------------------------------- embedding
def test_vectors_are_unit_normalised_so_cosine_is_a_dot_product():
    embedder = TfidfEmbedder().fit(["cobranca de boleto", "parcelamento da divida"])
    vectors = embedder.encode(["cobranca"])
    assert vectors.shape[0] == 1
    assert abs(float((vectors[0] ** 2).sum()) - 1.0) < 1e-6


def test_tfidf_must_see_the_corpus_before_encoding():
    with pytest.raises(RuntimeError, match="fit"):
        TfidfEmbedder().encode(["qualquer coisa"])


def test_character_ngrams_match_portuguese_inflection():
    """parcelamento/parcelar/parcelas should land near each other without a stemmer."""
    embedder = TfidfEmbedder().fit(
        ["opcoes de parcelamento da divida", "prazo de entrega do produto"]
    )
    vectors = embedder.encode(["quero parcelar", "opcoes de parcelamento da divida"])
    related = float(vectors[0] @ vectors[1])
    vectors = embedder.encode(["quero parcelar", "prazo de entrega do produto"])
    unrelated = float(vectors[0] @ vectors[1])
    assert related > unrelated


def test_an_unknown_embedder_is_rejected_by_name():
    with pytest.raises(ValueError, match="Unknown embedder"):
        get_embedder("magic")


# ------------------------------------------------------------------------ index
def test_search_returns_the_document_that_answers_the_question(index):
    assert index.search_documents("posso pagar com pix?", top_k=1) == ["faq.md"]
    # Within the top 2 rather than first: see the known weakness of the lexical baseline
    # on short questions, documented in docs/rag.md.
    assert "politica.md" in index.search_documents("voces ligam no domingo?", top_k=2)


def test_search_scores_are_ordered(index):
    hits = index.search("parcelamento", top_k=3)
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_searching_before_building_is_an_error(corpus):
    with pytest.raises(RuntimeError, match="build"):
        VectorIndex().add_documents(load_directory(corpus)).search("x")


def test_building_an_empty_index_is_an_error():
    with pytest.raises(ValueError, match="add documents"):
        VectorIndex().build()


def test_an_index_round_trips_through_disk(index, tmp_path):
    path = index.save(tmp_path / "index.joblib")
    restored = VectorIndex.load(path)
    assert len(restored) == len(index)
    assert restored.search_documents("pix", top_k=1) == index.search_documents("pix", top_k=1)


def test_stats_report_the_file_level_document_count(index):
    stats = index.stats()
    # Three CSV rows collapse to one document.
    assert stats["documents"] == 3
    assert stats["chunks"] >= 3
    assert stats["embedder"] == "tfidf"


# -------------------------------------------------------------------- evaluation
def test_retrieval_metrics_are_computed_against_labelled_questions(index):
    questions = [
        Question(question="posso pagar com pix?", relevant=frozenset({"faq.md"})),
        Question(question="voces ligam no domingo?", relevant=frozenset({"politica.md"})),
    ]
    scores = evaluate(index, questions, k=2)
    assert scores.recall == pytest.approx(1.0)
    assert scores.mrr >= 0.5
    assert scores.misses == []


def test_a_question_nothing_answers_is_reported_as_a_miss(index):
    questions = [Question(question="qual a cor do ceu?", relevant=frozenset({"inexistente.md"}))]
    scores = evaluate(index, questions, k=3)
    assert scores.recall == 0.0
    assert scores.misses == ["qual a cor do ceu?"]


def test_mrr_distinguishes_first_place_from_fifth(index):
    """precision@5 hides where in the list the answer sat; MRR does not."""
    question = [Question(question="posso pagar com pix?", relevant=frozenset({"faq.md"}))]
    assert evaluate(index, question, k=5).mrr == pytest.approx(1.0)
    wrong = [Question(question="posso pagar com pix?", relevant=frozenset({"tabela.csv"}))]
    assert evaluate(index, wrong, k=5).mrr < 1.0


def test_the_sweep_reports_every_k(index):
    questions = [Question(question="pix", relevant=frozenset({"faq.md"}))]
    table = sweep(index, questions, ks=(1, 3))
    assert list(table["k"]) == [1, 3]
    assert {"precision@k", "recall@k", "mrr"} <= set(table.columns)


def test_questions_load_from_the_labelled_file(tmp_path):
    path = tmp_path / "questions.json"
    path.write_text(
        json.dumps([{"question": "q", "relevant": ["a.md", "b.md"], "note": "n"}]),
        encoding="utf-8",
    )
    questions = load_questions(path)
    assert questions[0].relevant == frozenset({"a.md", "b.md"})
    assert questions[0].note == "n"


def test_the_shipped_knowledge_base_answers_its_own_questions():
    """Guards the real corpus, not a fixture: a bad edit to a policy file shows up here."""
    from collection.config import settings

    index = VectorIndex().add_documents(load_directory(settings.dir_knowledge)).build()
    questions = load_questions(settings.dir_knowledge / "questions.json")
    scores = evaluate(index, questions, k=5)
    assert scores.recall >= 0.9
    assert scores.mrr >= 0.7
