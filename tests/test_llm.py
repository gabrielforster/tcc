"""The LLM responder, the prompt it builds and the faithfulness it is held to.

No test here touches the network. The provider sits behind `LLMClient`, so a scripted
client gives exact control over what the "model" says — which is what makes it possible to
test the cases that matter and cannot be summoned on demand from a real model: the
invented interest rate, the citation of a document that was never retrieved, the API
falling over mid-conversation.

The emphasis is the same as `test_responsive.py`: a collection system that invents an
instalment plan does more damage than one that hands over, so the refusal, fallback and
hallucination-detection paths get more tests than the happy path.
"""

import pathlib

import pytest

from collection.agents.events import Event, EventType
from collection.agents.intent import Intent
from collection.agents.responsive import ResponsiveAgent, TemplateResponder, get_responder
from collection.config import settings
from collection.llm import faithfulness as faith
from collection.llm.client import EchoClient, LLMClient, LLMError, ScriptedClient, get_client
from collection.llm.evaluate import LATENCY_BUDGET_MS, compare, run
from collection.llm.prompt import HANDOVER, REFUSAL, SYSTEM_PROMPT, build_user_prompt
from collection.llm.responder import LLMResponder
from collection.rag.documents import load_directory
from collection.rag.evaluate import Question, load_questions
from collection.rag.index import VectorIndex


@pytest.fixture(scope="module")
def index():
    return VectorIndex().add_documents(load_directory(settings.dir_knowledge)).build()


@pytest.fixture(scope="module")
def questions():
    return load_questions(settings.dir_knowledge / "questions.json")


@pytest.fixture
def passages(index):
    return index.search("posso parcelar minha divida?", top_k=3)


def scripted(*replies: str) -> ScriptedClient:
    return ScriptedClient(replies=list(replies))


# ---------------------------------------------------------------------- prompt
def test_the_prompt_carries_the_retrieved_passages(passages):
    prompt = build_user_prompt("posso parcelar?", Intent.NEGOTIATION, passages)
    for hit in passages:
        # Whitespace is collapsed when formatting, so compare on the first words.
        opening = " ".join(hit.text.split()[:6])
        assert opening in prompt
        assert hit.document_id in prompt


def test_the_prompt_names_the_refusal_token(passages):
    assert REFUSAL in SYSTEM_PROMPT
    assert REFUSAL in build_user_prompt("qualquer coisa", Intent.UNKNOWN, [])


def test_the_system_prompt_states_the_legal_prohibitions():
    for prohibition in ("CDC art. 42", "Ameaçar", "desconto"):
        assert prohibition in SYSTEM_PROMPT


# ------------------------------------------------------------------- responder
def test_an_answer_is_returned_and_tagged_with_its_responder(passages):
    responder = LLMResponder(client=scripted("Você pode parcelar.\nFonte: faq.md"))
    answer = responder.respond("posso parcelar?", Intent.NEGOTIATION, passages)
    assert answer.grounded
    assert answer.responder == "llm"


def test_no_passages_means_no_call_to_the_model():
    client = scripted("this should never be used")
    answer = LLMResponder(client=client).respond("?", Intent.UNKNOWN, [])
    assert not answer.grounded
    assert answer.text == HANDOVER
    assert client.calls == [], "the model was asked to answer with no evidence"


@pytest.mark.parametrize(
    "reply",
    [
        REFUSAL,
        f"{REFUSAL}.",
        f"  {REFUSAL}  ",
        "",
        # qwen2.5:3b, 2026-11-06: the token followed by the citation line the prompt asks for.
        f"{REFUSAL} Fonte: (politica-de-cobranca.md)",
    ],
)
def test_a_refusal_is_not_grounded_and_reaches_a_human(passages, reply):
    answer = LLMResponder(client=scripted(reply)).respond("?", Intent.NEGOTIATION, passages)
    assert not answer.grounded
    assert answer.text == HANDOVER


def test_generation_failure_falls_back_to_the_template(passages):
    # An empty scripted client raises LLMError on the first call, standing in for an
    # expired key, a rate limit or a timeout.
    responder = LLMResponder(client=scripted())
    answer = responder.respond("posso parcelar?", Intent.NEGOTIATION, passages)
    assert answer.grounded, "the customer should still get an answer"
    assert answer.responder == "llm:fallback", "the fallback must not pass as the LLM"
    assert responder.failures


def test_a_citation_of_an_unretrieved_document_is_dropped(passages):
    responder = LLMResponder(client=scripted("Pode parcelar.\nFonte: inventado.md"))
    answer = responder.respond("posso parcelar?", Intent.NEGOTIATION, passages)
    assert "inventado.md" not in answer.citations


def test_a_missing_citation_line_credits_the_retrieved_documents(passages):
    responder = LLMResponder(client=scripted("Pode parcelar em ate 6 vezes."))
    answer = responder.respond("posso parcelar?", Intent.NEGOTIATION, passages)
    assert answer.citations == list(dict.fromkeys(p.document_id for p in passages))


def test_the_agent_escalates_a_refusal(index):
    agent = ResponsiveAgent(index=index, responder=LLMResponder(client=scripted(REFUSAL)))
    event = Event(
        type=EventType.MESSAGE_RECEIVED,
        customer_id="cus_1",
        payload={"text": "qual o valor do meu boleto?"},
    )
    produced = agent.handle(event)
    assert [e.type for e in produced] == [EventType.ESCALATED_TO_HUMAN]
    assert produced[0].payload["reason"] == "no_grounding"


# ---------------------------------------------------------------- faithfulness
def test_a_quoted_answer_is_faithful(passages):
    quoted = " ".join(passages[0].text.split()[:40])
    scores = faith.evaluate(f"{quoted}\nFonte: {passages[0].document_id}", passages)
    assert scores.faithful
    assert scores.citations_valid


def test_an_invented_figure_is_caught(passages):
    scores = faith.evaluate("Você pode parcelar em 97 vezes com juros de 0,37% ao mês.", passages)
    assert not scores.faithful
    assert "97" in scores.unsupported_numbers


def test_an_invented_sentence_is_caught(passages):
    scores = faith.evaluate(
        "O seu nome foi retirado de todos os cadastros de proteção ao crédito hoje cedo.",
        passages,
    )
    assert not scores.faithful
    assert scores.unsupported_sentences


def test_a_citation_of_an_unseen_document_is_invalid(passages):
    scores = faith.evaluate("Pode parcelar.\nFonte: nao-existe.md", passages)
    assert not scores.citations_valid
    assert scores.invalid_citations == ["nao-existe.md"]


def test_an_answer_with_no_passages_supports_nothing():
    scores = faith.evaluate("Você deve R$ 430,00.", [])
    assert not scores.faithful
    assert scores.sentence_support == 0.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.234,56", "1234.56"),  # pt-BR grouping and decimal
        ("1234.56", "1234.56"),  # en decimal
        ("1.234", "1234"),  # grouping, not a decimal
        ("1.5", "1.5"),  # a decimal, not grouping
        ("6", "6"),
        ("45,", "45"),  # trailing punctuation
    ],
)
def test_numbers_are_normalised_before_matching(raw, expected):
    assert faith.normalise_number(raw) == expected


def test_formatting_alone_is_not_a_hallucination(index):
    """`1.234,56` and `1234.56` are one figure, and reporting two would be noise."""
    passages = index.search("qual o valor minimo da parcela?", top_k=3)
    supported = {n for hit in passages for n in faith.numbers_in(hit.text)}
    assert supported, "the retrieved passages carry no figures to rewrite"
    number = sorted(supported, key=len)[-1]
    rewritten = f"{float(number):,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")
    scores = faith.evaluate(f"O valor é R$ {rewritten}.", passages)
    assert not scores.unsupported_numbers, f"{rewritten} is {number} written in pt-BR"


def test_the_citation_line_is_not_scored_as_a_claim(passages):
    # "Fonte: tabela-de-parcelamento.csv" is evidence; scoring it as prose would punish
    # an answer for citing correctly.
    assert faith.sentences_in("Pode parcelar sim.\nFonte: faq.md") == ["Pode parcelar sim."]
    assert faith.citations_in("Pode parcelar sim.\nFonte: faq.md, outro.md") == [
        "faq.md",
        "outro.md",
    ]


# ------------------------------------------------------------------ evaluation
def test_the_template_arm_runs_without_a_key(index, questions):
    scores = run(index, questions[:5], responder="template")
    assert scores.questions == 5
    assert scores.latency_p95 <= LATENCY_BUDGET_MS
    assert scores.fallbacks == 0


def test_the_template_arm_never_hallucinates(index, questions):
    """The ceiling the LLM is measured against: quoting cannot invent."""
    scores = run(index, questions, responder=TemplateResponder())
    assert scores.faithful_rate == 1.0


def test_each_question_is_answered_under_its_own_customer_id(index, questions):
    # Shared ids would trip the "three unclear messages" rule partway through a run and
    # attribute the escalations to the responder.
    scores = run(index, questions, responder="template")
    reasons = {record.escalated for record in scores.records}
    assert reasons != {True}, "every question escalated: the unclear counter leaked"


def test_comparing_two_arms_scores_both(index, questions):
    llm = LLMResponder(client=scripted(*[f"Resposta {i}.\nFonte: faq.md" for i in range(60)]))
    table, results = compare(index, questions[:4], ["template", llm])
    assert list(table["responder"]) == ["template", "llm"]
    assert len(results) == 2


def test_a_run_of_fallbacks_is_reported_not_hidden(index, questions):
    scores = run(index, questions[:3], responder=LLMResponder(client=scripted()))
    assert scores.fallbacks == scores.questions


def test_the_report_names_the_fallbacks(index, questions, tmp_path):
    from collection.llm.evaluate import write_report

    table, results = compare(index, questions[:2], [LLMResponder(client=scripted())])
    report = write_report(table, results, tmp_path / "responder-comparison.md")
    text = pathlib.Path(report).read_text(encoding="utf-8")
    assert "fell back to the template responder" in text


# ---------------------------------------------------------------------- client
def test_an_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        get_client("gpt-3-but-local")


def test_the_anthropic_client_without_a_key_raises_a_wrapped_error(monkeypatch):
    from collection.llm.client import AnthropicClient

    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY|anthropic SDK"):
        AnthropicClient().complete("system", "user")


class _FakeAnthropic:
    """Stands in for the SDK's client: records the request and returns a canned response."""

    requests: list[dict] = []
    raises: Exception | None = None

    def __init__(self, api_key: str, timeout: float) -> None:
        self.api_key = api_key
        self.messages = self

    def create(self, **request):
        type(self).requests.append(request)
        if type(self).raises is not None:
            raise type(self).raises
        from types import SimpleNamespace as Reply

        # A leading space, a non-text block and an empty one: only the text survives, trimmed.
        content = [
            Reply(type="text", text=" Os juros são de 1% ao mês."),
            Reply(type="tool_use"),
            Reply(type="text", text=""),
        ]
        return Reply(content=content, usage=Reply(input_tokens=120, output_tokens=30))


@pytest.fixture
def fake_sdk(monkeypatch):
    import sys
    import types

    _FakeAnthropic.requests = []
    _FakeAnthropic.raises = None
    module = types.ModuleType("anthropic")
    module.Anthropic = _FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", module)
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    return _FakeAnthropic


def test_the_anthropic_client_sends_a_reproducible_request_and_reads_text_only(fake_sdk):
    from collection.llm.client import AnthropicClient

    completion = AnthropicClient(model="claude-test").complete("system", "user")

    [request] = fake_sdk.requests
    assert request["temperature"] == 0.0, "the same question must get the same plan"
    assert request["max_tokens"] == 512
    assert request["system"] == "system"
    assert request["messages"] == [{"role": "user", "content": "user"}]
    assert completion.text == "Os juros são de 1% ao mês."
    assert completion.model == "claude-test"
    assert completion.tokens == 150


def test_an_sdk_error_becomes_an_llm_error_the_responder_can_fall_back_on(fake_sdk):
    from collection.llm.client import AnthropicClient

    fake_sdk.raises = TimeoutError("read timed out")
    with pytest.raises(LLMError, match="TimeoutError: read timed out"):
        AnthropicClient().complete("system", "user")


def test_a_missing_sdk_is_an_llm_error_that_says_what_to_install(monkeypatch):
    import sys

    from collection.llm.client import AnthropicClient

    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(LLMError, match="uv sync --extra llm"):
        AnthropicClient().complete("system", "user")


class _FakeResponse:
    def __init__(self, status_code: int, body=None, text: str = "") -> None:
        self.status_code = status_code
        self._body = body
        self.text = text or str(body)

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class _FakeSession:
    """Stands in for `requests.Session`: records the request, returns a prepared reply."""

    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.requests: list[dict] = []

    def post(self, url, json, headers, timeout):
        self.requests.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return self.response


def _openai(session, **kwargs):
    from collection.llm.client import OpenAICompatibleClient

    return OpenAICompatibleClient(
        model="qwen2.5:3b", base_url="http://llm.test/v1/", session=session, **kwargs
    )


def _chat_body(text: str) -> dict:
    return {
        "model": "qwen2.5:3b",
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30},
    }


def test_the_openai_client_sends_one_deterministic_chat_request():
    session = _FakeSession(_FakeResponse(200, _chat_body("  Pode sim.  ")))
    completion = _openai(session, api_key="").complete("system text", "user text")

    request = session.requests[0]
    assert request["url"] == "http://llm.test/v1/chat/completions"
    assert request["json"]["messages"] == [
        {"role": "system", "content": "system text"},
        {"role": "user", "content": "user text"},
    ]
    assert request["json"]["temperature"] == 0.0
    assert request["json"]["max_tokens"] == 512
    # A local server needs no key, so none is sent.
    assert "Authorization" not in request["headers"]
    assert completion.text == "Pode sim."
    assert (completion.input_tokens, completion.output_tokens) == (120, 30)
    assert completion.model == "qwen2.5:3b"


def test_the_openai_client_sends_a_key_only_when_one_is_set():
    session = _FakeSession(_FakeResponse(200, _chat_body("ok")))
    _openai(session, api_key="sk-test").complete("s", "u")
    assert session.requests[0]["headers"]["Authorization"] == "Bearer sk-test"


@pytest.mark.parametrize(
    "session",
    [
        _FakeSession(error=ConnectionError("refused")),
        _FakeSession(_FakeResponse(500, text="model not loaded")),
        _FakeSession(_FakeResponse(200, None, text="<html>")),
        _FakeSession(_FakeResponse(200, {"choices": []})),
    ],
    ids=["transport", "http-error", "not-json", "bad-shape"],
)
def test_every_openai_client_failure_is_an_llm_error(session):
    with pytest.raises(LLMError):
        _openai(session).complete("s", "u")


def test_an_openai_client_failure_still_falls_back_to_the_template(passages):
    responder = LLMResponder(client=_openai(_FakeSession(error=TimeoutError("slow"))))
    answer = responder.respond("posso parcelar?", Intent.NEGOTIATION, passages)
    assert answer.responder == "llm:fallback"
    assert responder.failures


def test_the_openai_provider_is_registered(monkeypatch):
    from collection.llm.client import OpenAICompatibleClient, describe

    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "llm_model", "qwen2.5:3b")
    assert isinstance(get_client(), OpenAICompatibleClient)
    assert "qwen2.5:3b" in describe() and "openai" in describe()


def test_the_echo_client_is_a_client():
    completion = EchoClient().complete("system", "user text")
    assert isinstance(EchoClient(), LLMClient)
    assert completion.text == "user text"
    assert completion.tokens == 0


def test_the_factory_builds_both_responders():
    assert isinstance(get_responder("template"), TemplateResponder)
    assert get_responder("llm").name == "llm"
    with pytest.raises(ValueError, match="Unknown responder"):
        get_responder("telepathy")


def test_scripted_client_records_what_it_was_asked(passages):
    client = scripted("ok")
    LLMResponder(client=client).respond("posso parcelar?", Intent.NEGOTIATION, passages)
    system, user = client.calls[0]
    assert system == SYSTEM_PROMPT
    assert "posso parcelar?" in user


def test_a_question_set_is_a_question_set(questions):
    assert questions and all(isinstance(q, Question) for q in questions)
