"""Replies shaped for the ear: short enough to sit through, still nothing but the documents."""

import json
import math
import struct
from pathlib import Path

import pytest

from collection.agents.events import Event, EventType
from collection.agents.intent import Intent
from collection.agents.responsive import (
    CONFIRM_PREFIX,
    Answer,
    Responder,
    ResponsiveAgent,
    TemplateResponder,
)
from collection.agents.spoken import select, sentences_of
from collection.channels.voice_call import REPLY_MAX_WORDS, REPLY_TARGET_S, VoiceCallChannel
from collection.channels.web import ChatChannel
from collection.config import settings
from collection.llm import faithfulness
from collection.llm.client import ScriptedClient
from collection.llm.prompt import HANDOVER
from collection.llm.responder import LLMResponder
from collection.rag.chunking import Chunk
from collection.rag.index import Retrieved
from collection.voice.audio import ffmpeg_available
from collection.voice.speak import (
    SLOWEST_WORDS_PER_SECOND,
    ToneSynthesizer,
    speaking_rates,
    spoken_words,
    write_wav,
)
from collection.voice.transcribe import ScriptedTranscriber

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg is not installed")


def _questions() -> list[str]:
    asked = [item["question"] for item in json.loads(_knowledge("questions.json"))]
    voice = json.loads(_knowledge("voice-messages.json"))
    voice = voice if isinstance(voice, list) else voice["messages"]
    return asked + [item["text"] for item in voice]


def _knowledge(name: str) -> str:
    return (settings.dir_knowledge / name).read_text(encoding="utf-8")


def _passage(text: str, document_id: str = "doc.md") -> Retrieved:
    return Retrieved(chunk=Chunk(text=text, document_id=document_id, ordinal=0), score=1.0)


class Capturing(Responder):
    """Records the budget each turn arrived with."""

    name = "capturing"

    def __init__(self) -> None:
        self.budgets: list[int | None] = []

    def respond(self, message, intent, passages, history=None, *, max_words=None):
        self.budgets.append(max_words)
        return Answer(text="ok", citations=[p.document_id for p in passages])


# --------------------------------------------------------------------- the budget
def test_the_word_budget_is_derived_from_the_slowest_measured_rate():
    assert int(REPLY_TARGET_S * SLOWEST_WORDS_PER_SECOND) == REPLY_MAX_WORDS
    assert VoiceCallChannel.capabilities.max_reply_words == REPLY_MAX_WORDS
    assert ChatChannel.capabilities.max_reply_words is None


def test_every_sentence_of_the_corpus_fits_the_budget_on_its_own(index):
    """`select` speaks an over-long first sentence anyway; the corpus must not have one."""
    for chunk in index.chunks:
        for sentence in sentences_of(_passage(chunk.text, chunk.document_id)):
            assert sentence.words <= REPLY_MAX_WORDS, sentence.text


def test_spoken_words_count_what_a_synthesiser_says_not_what_split_finds():
    # An identifier is three words aloud, a decimal is two numbers, "%" is "por cento".
    assert spoken_words("entrada_minima_percentual: 1.5%") == 3 + 2 + 2
    assert spoken_words("A segunda via sai aqui.") == 5


def test_speaking_rates_are_words_over_seconds():
    rates = speaking_rates(ToneSynthesizer(seconds_per_char=0.1), ["um dois três"])
    assert rates == pytest.approx([3 / (len("um dois três") * 0.1)], rel=0.01)


# ---------------------------------------------------------------- the selection
def test_a_passage_is_read_without_headings_or_list_markers():
    passage = _passage(
        "# Política\n\n## Prazos\n\nO prazo é de dois dias.\nSem exceções.\n\n"
        '- *"Já paguei"*: pedir o comprovante.\n'
    )
    found = sentences_of(passage)
    assert [s.text for s in found] == [
        "O prazo é de dois dias.",
        "Sem exceções.",
        '"Já paguei": pedir o comprovante.',
    ]
    assert {s.heading for s in found} == {"Prazos"}


def test_the_answering_section_is_spoken_in_order_and_nothing_else():
    passage = _passage(
        "## Segunda via\n\nA segunda via sai nesta conversa. O boleto vence no próximo dia útil."
        "\n\n## Juros\n\nO atraso gera multa de 2%."
    )
    chosen = select("preciso da segunda via", [passage], max_words=40)
    assert [s.text for s in chosen] == [
        "A segunda via sai nesta conversa.",
        "O boleto vence no próximo dia útil.",
    ]


def test_a_sentence_that_does_not_fit_ends_the_reply_and_is_never_cut():
    passage = _passage("## Segunda via\n\nA segunda via sai aqui. " + "palavra " * 30 + "fim.")
    chosen = select("segunda via", [passage], max_words=10)
    assert [s.text for s in chosen] == ["A segunda via sai aqui."]


def test_a_first_sentence_longer_than_the_budget_is_still_spoken():
    passage = _passage("## Segunda via\n\n" + "palavra " * 20 + "segunda via.")
    assert len(select("segunda via", [passage], max_words=5)) == 1


# ------------------------------------------------------------- the template arm
def test_a_spoken_template_reply_fits_and_quotes_only_the_passages(index):
    agent = ResponsiveAgent(index=index)
    spoken = 0
    for question in _questions():
        reply = agent.answer(question, max_words=REPLY_MAX_WORDS)
        if not reply.passages:
            continue
        spoken += 1
        text = reply.answer.text
        assert spoken_words(text) <= REPLY_MAX_WORDS, question
        assert "Fonte:" not in text
        assert reply.answer.citations
        verdict = faithfulness.evaluate(text, reply.passages)
        assert verdict.faithful, (question, verdict.unsupported_sentences)
        assert verdict.sentence_support == 1.0, question
    assert spoken >= 20


def test_without_a_budget_the_template_reply_is_unchanged(index):
    passages = index.search("parcelamento", top_k=2)
    written = TemplateResponder().respond("posso parcelar?", Intent.NEGOTIATION, passages)
    assert written.text.endswith(
        "Fonte: " + ", ".join(dict.fromkeys(p.document_id for p in passages[:2]))
    )
    assert " ".join(passages[0].text.split())[:60] in written.text


def test_a_budget_changes_neither_the_handover_nor_the_escalation(index):
    # Separate agents: the unclear-message counter is per customer and would carry over.
    for question in _questions():
        read = ResponsiveAgent(index=index).answer(question)
        heard = ResponsiveAgent(index=index).answer(question, max_words=REPLY_MAX_WORDS)
        assert heard.classification.intent == read.classification.intent, question
        assert (heard.escalated, heard.escalation_reason) == (
            read.escalated,
            read.escalation_reason,
        ), question
        assert [p.chunk.chunk_id for p in heard.passages] == [
            p.chunk.chunk_id for p in read.passages
        ]
    dispute = ResponsiveAgent(index=index).answer(
        "essa dívida não é minha eu nunca contratei esse serviço", max_words=REPLY_MAX_WORDS
    )
    assert dispute.escalated
    none = TemplateResponder().respond("x", Intent.UNKNOWN, [], max_words=REPLY_MAX_WORDS)
    assert not none.grounded


# ----------------------------------------------------------------- the LLM arm
def test_the_llm_is_told_the_budget_and_its_citation_line_is_not_spoken():
    client = ScriptedClient(replies=["A segunda via sai nesta conversa.\nFonte: faq.md"])
    passage = _passage("## Segunda via\n\nA segunda via sai nesta conversa.", "faq.md")
    answer = LLMResponder(client=client).respond(
        "segunda via", Intent.SECOND_COPY, [passage], max_words=REPLY_MAX_WORDS
    )
    system, _ = client.calls[0]
    assert f"No máximo {REPLY_MAX_WORDS} palavras" in system
    assert answer.text == "A segunda via sai nesta conversa."
    assert answer.citations == ["faq.md"]
    assert answer.responder == "llm"


def test_an_llm_reply_over_budget_falls_back_to_the_spoken_template():
    client = ScriptedClient(replies=["palavra " * 60 + "\nFonte: faq.md"])
    passage = _passage("## Segunda via\n\nA segunda via sai nesta conversa.", "faq.md")
    answer = LLMResponder(client=client).respond(
        "segunda via", Intent.SECOND_COPY, [passage], max_words=REPLY_MAX_WORDS
    )
    assert answer.responder == "llm:over_budget"
    assert answer.text == "Sobre a segunda via: A segunda via sai nesta conversa."


def test_a_spoken_refusal_is_still_a_handover():
    client = ScriptedClient(replies=["NAO_SEI Fonte: faq.md"])
    answer = LLMResponder(client=client).respond(
        "x", Intent.UNKNOWN, [_passage("## A\n\nTexto.")], max_words=REPLY_MAX_WORDS
    )
    assert answer.text == HANDOVER and not answer.grounded


def test_without_a_budget_the_llm_prompt_is_unchanged():
    client = ScriptedClient(replies=["Pode sim.\nFonte: faq.md"])
    LLMResponder(client=client).respond("x", Intent.UNKNOWN, [_passage("## A\n\nPode sim.")])
    system, _ = client.calls[0]
    assert "FALADA" not in system


# ------------------------------------------------------------- the agent seam
def _audio_event(budget: int | None, path: str = "x.wav") -> Event:
    payload: dict[str, object] = {"audio_path": path}
    if budget is not None:
        payload["reply_max_words"] = budget
    return Event(type=EventType.AUDIO_RECEIVED, customer_id="cus_1", payload=payload)


def test_a_read_back_is_paid_for_out_of_the_answers_budget(index, tmp_path):
    heard = "quero parcelar em 6 vezes"
    responder = Capturing()
    agent = ResponsiveAgent(
        index=index,
        responder=responder,
        transcriber=ScriptedTranscriber(transcripts={"x": heard}),
    )
    agent.handle(_audio_event(REPLY_MAX_WORDS, str(_tone_clip(tmp_path, "x"))))
    read_back = len(CONFIRM_PREFIX.format(transcript=heard).split())
    assert responder.budgets == [REPLY_MAX_WORDS - read_back]


def test_a_turn_with_no_budget_is_answered_for_a_reader(index):
    responder = Capturing()
    agent = ResponsiveAgent(index=index, responder=responder)
    agent.handle(
        Event(type=EventType.MESSAGE_RECEIVED, customer_id="cus_1", payload={"text": "segunda via"})
    )
    assert responder.budgets == [None]


def _tone_clip(tmp_path: Path, stem: str) -> Path:
    pcm = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * i / 16_000)))
        for i in range(16_000)
    )
    return write_wav(tmp_path / f"{stem}.wav", pcm, 16_000)


@needs_ffmpeg
def test_the_call_asks_for_its_budget_and_the_chat_asks_for_none(index, tmp_path):
    transcripts = {"segunda_via": "preciso da segunda via do boleto"}
    call_responder, chat_responder = Capturing(), Capturing()
    call = VoiceCallChannel(
        retriever=index,
        responder=call_responder,
        transcriber=ScriptedTranscriber(transcripts=transcripts),
        audio_dir=tmp_path / "calls",
    )
    started = call.start()
    call.opening_played(started.call_id)
    call.hear(started.call_id, _tone_clip(tmp_path, "segunda_via"))
    assert call_responder.budgets == [REPLY_MAX_WORDS]

    chat = ChatChannel(
        retriever=index,
        responder=chat_responder,
        transcriber=ScriptedTranscriber(transcripts=transcripts),
    )
    session = chat.store.open(None)
    chat.send(session.session_id, "preciso da segunda via do boleto")
    chat.send_audio(session.session_id, _tone_clip(tmp_path, "segunda_via"))
    assert chat_responder.budgets == [None, None]
