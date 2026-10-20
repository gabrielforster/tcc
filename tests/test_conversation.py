"""Multi-turn: resolving follow-ups without losing the safety rules.

Two properties carry most of these tests.

**Context may only add.** A follow-up is retrieved on alongside the customer's own words,
never instead of them, so a context strategy cannot lose a passage the bare message would
have found. That is a guarantee, not a tendency, and
`test_context_never_loses_a_passage_the_message_alone_finds` is the guard on it. It exists
because the first implementation did lose them: measured follow-up hit rate fell from 0.89
to 0.78 when the contextual query replaced the message.

**Inheritance must not swallow the handover.** A customer whose messages nobody
understands has to reach a person after three tries. Inheriting the thread's intent makes
"sim" answerable, and the risk is that it also makes gibberish answerable forever. The
rule is keyed on vocabulary rather than confidence, because a measurement showed
confidence cannot tell them apart -- gibberish scores *higher* than a bare "sim".
"""

import pytest

from collection.agents.conversation import (
    CONTEXT_WINDOW,
    ConcatRewriter,
    Conversation,
    LLMRewriter,
    NoContextRewriter,
    build_vocabulary,
    get_rewriter,
    is_follow_up,
    known_ratio,
    resolve_intent,
)
from collection.agents.conversation_eval import compare, load_dialogues, run
from collection.agents.intent import Intent, IntentClassifier
from collection.agents.responsive import TemplateResponder
from collection.config import settings
from collection.llm.client import ScriptedClient
from collection.rag.documents import load_directory
from collection.rag.index import VectorIndex


@pytest.fixture(scope="module")
def index():
    return VectorIndex().add_documents(load_directory(settings.dir_knowledge)).build()


@pytest.fixture(scope="module")
def classifier():
    return IntentClassifier()


@pytest.fixture(scope="module")
def vocabulary(index, classifier):
    return build_vocabulary([chunk.text for chunk in index.chunks], classifier)


@pytest.fixture(scope="module")
def dialogues():
    return load_dialogues(settings.dir_knowledge / "conversations.json")


def thread(intent: str = "negotiation") -> Conversation:
    return (
        Conversation()
        .add("customer", "posso parcelar minha divida?", intent)
        .add("agent", "Sobre as condições de parcelamento: ...")
    )


# -------------------------------------------------------------- the conversation
def test_a_fresh_conversation_has_no_context():
    conversation = Conversation()
    assert not conversation
    assert conversation.last_intent() is None
    assert conversation.last_customer_message() is None


def test_the_window_keeps_only_recent_turns():
    conversation = Conversation()
    for i in range(10):
        conversation.add("customer", f"mensagem {i}", "debt_inquiry")
    assert len(conversation.window()) == CONTEXT_WINDOW
    assert conversation.window()[-1].text == "mensagem 9"


def test_the_last_intent_skips_uninformative_ones():
    conversation = (
        Conversation()
        .add("customer", "posso parcelar?", "negotiation")
        .add("customer", "bom dia", "small_talk")
        .add("customer", "???", "unknown")
    )
    assert conversation.last_intent() is Intent.NEGOTIATION


def test_the_transcript_labels_both_sides():
    text = thread().transcript()
    assert "Cliente: posso parcelar minha divida?" in text
    assert "Atendimento:" in text


# ------------------------------------------------------------------- follow-ups
@pytest.mark.parametrize("message", ["sim", "6", "e em 6 vezes?", "e a entrada?"])
def test_short_messages_in_a_thread_are_follow_ups(message):
    assert is_follow_up(message, thread())


def test_a_long_message_is_not_a_follow_up():
    assert not is_follow_up(
        "quero entender melhor quais sao as condicoes de parcelamento disponiveis", thread()
    )


def test_nothing_is_a_follow_up_without_a_thread():
    assert not is_follow_up("sim", None)
    assert not is_follow_up("sim", Conversation())


# -------------------------------------------------------------------- rewriting
def test_no_context_returns_the_message_alone():
    assert NoContextRewriter().queries("e em 6 vezes?", thread()) == ["e em 6 vezes?"]


def test_concat_keeps_the_message_as_its_own_query():
    """The guarantee: the customer's words are always retrieved on."""
    queries = ConcatRewriter().queries("e em 6 vezes?", thread())
    assert queries[0] == "e em 6 vezes?"
    assert "posso parcelar minha divida?" in queries[1]


def test_a_standalone_message_is_not_expanded():
    message = "quero a segunda via do boleto por favor agora"
    assert ConcatRewriter().queries(message, thread()) == [message]


def test_the_llm_rewriter_keeps_the_message_too():
    rewriter = LLMRewriter(client=ScriptedClient(replies=["Em quantas parcelas posso dividir?"]))
    queries = rewriter.queries("e em 6 vezes?", thread())
    assert queries == ["e em 6 vezes?", "Em quantas parcelas posso dividir?"]


def test_the_llm_rewriter_falls_back_when_generation_fails():
    rewriter = LLMRewriter(client=ScriptedClient(replies=[]))
    assert rewriter.queries("e em 6 vezes?", thread()) == ConcatRewriter().queries(
        "e em 6 vezes?", thread()
    )


def test_a_runaway_rewrite_is_discarded():
    rewriter = LLMRewriter(client=ScriptedClient(replies=[" ".join(["palavra"] * 80)]))
    assert rewriter.queries("e em 6 vezes?", thread()) == ConcatRewriter().queries(
        "e em 6 vezes?", thread()
    )


def test_an_unknown_rewriter_is_rejected():
    with pytest.raises(ValueError, match="Unknown rewriter"):
        get_rewriter("telepathy")


# ----------------------------------------------------------- intent inheritance
def test_a_bare_affirmation_inherits_the_thread_intent(classifier, vocabulary):
    resolved = resolve_intent(classifier.classify("sim"), "sim", thread(), vocabulary)
    assert resolved.intent is Intent.NEGOTIATION
    assert resolved.inherited
    assert resolved.is_confident, "an inherited intent counts as understood"


def test_a_bare_number_inherits_too(classifier, vocabulary):
    assert resolve_intent(classifier.classify("6"), "6", thread(), vocabulary).inherited


def test_gibberish_does_not_inherit(classifier, vocabulary):
    """The measurement that forced the design.

    'asdfgh qwerty zxcvb' scores *higher* on the lexical classifier than a bare 'sim'
    does, so confidence cannot separate them. Vocabulary can, and must: without this a
    customer typing nonsense would inherit an intent forever and never reach a person.
    """
    message = "asdfgh qwerty zxcvb"
    resolved = resolve_intent(classifier.classify(message), message, thread(), vocabulary)
    assert not resolved.inherited
    assert not resolved.is_confident


def test_a_confident_message_is_never_overridden(classifier, vocabulary):
    message = "quero a segunda via do boleto"
    resolved = resolve_intent(classifier.classify(message), message, thread(), vocabulary)
    assert resolved.intent is Intent.SECOND_COPY
    assert not resolved.inherited


def test_nothing_is_inherited_without_a_prior_intent(classifier, vocabulary):
    empty = Conversation().add("customer", "oi", "small_talk")
    assert not resolve_intent(classifier.classify("sim"), "sim", empty, vocabulary).inherited


def test_known_words_include_digits_and_continuations(vocabulary):
    assert known_ratio("6", vocabulary) == 1.0
    assert known_ratio("sim", vocabulary) == 1.0
    assert known_ratio("asdfgh qwerty zxcvb", vocabulary) == 0.0


# ----------------------------------------------------------------- the agent
def test_a_follow_up_is_answered_rather_than_escalated(index):
    from collection.agents.responsive import ResponsiveAgent

    agent = ResponsiveAgent(index=index, responder=TemplateResponder())
    conversation = Conversation()
    first = agent.answer("posso parcelar minha divida?", "cus_1", conversation)
    conversation.add("customer", "posso parcelar minha divida?", first.classification.intent.value)
    conversation.add("agent", first.answer.text)

    second = agent.answer("sim", "cus_1", conversation)
    assert not second.escalated
    assert second.classification.inherited


def test_repeated_gibberish_still_reaches_a_person(index):
    """Inheritance must not disarm the three-strikes rule."""
    from collection.agents.responsive import ResponsiveAgent

    agent = ResponsiveAgent(index=index, responder=TemplateResponder())
    conversation = Conversation()
    first = agent.answer("posso parcelar minha divida?", "cus_2", conversation)
    conversation.add("customer", "posso parcelar minha divida?", first.classification.intent.value)

    replies = []
    for _ in range(3):
        reply = agent.answer("asdfgh qwerty zxcvb", "cus_2", conversation)
        conversation.add("customer", "asdfgh qwerty zxcvb", reply.classification.intent.value)
        replies.append(reply)
    assert replies[-1].escalated
    assert replies[-1].escalation_reason == "unclear_repeatedly"


def test_the_reply_reports_every_query_it_retrieved_on(index):
    from collection.agents.responsive import ResponsiveAgent

    agent = ResponsiveAgent(index=index, responder=TemplateResponder())
    reply = agent.answer("e em 6 vezes?", "cus_3", thread())
    assert reply.queries[0] == "e em 6 vezes?"
    assert len(reply.queries) == 2


def test_context_never_loses_a_passage_the_message_alone_finds(index, dialogues):
    """The guarantee the multi-query design exists for.

    Measured during development: a single contextual query dropped follow-up hit rate
    from 0.89 to 0.78, and merging the union *by score* still lost one, because a longer
    query scores higher against long passages. Interleaving by rank is what makes this
    hold.
    """
    without = run(index, dialogues, "none", TemplateResponder())
    with_context = run(index, dialogues, "concat", TemplateResponder())
    assert with_context.follow_up_hit_rate >= without.follow_up_hit_rate
    assert with_context.hit_rate >= without.hit_rate


def test_an_oracle_rewrite_recovers_the_follow_up_the_baseline_misses(index):
    """Isolates the bottleneck: retrieval can find it, the lexical rewriter cannot write it.

    'e em 6 vezes?' is the one labelled follow-up neither baseline retrieves. A rewrite
    that names what the customer means does retrieve it, so the failure belongs to query
    formulation, not to the index — which is exactly the hypothesis the LLM arm tests.
    """
    from collection.agents.responsive import ResponsiveAgent

    oracle = LLMRewriter(
        client=ScriptedClient(replies=["em quantas parcelas posso dividir a minha divida?"])
    )
    agent = ResponsiveAgent(index=index, responder=TemplateResponder(), rewriter=oracle)
    reply = agent.answer("e em 6 vezes?", "cus_4", thread())
    retrieved = {hit.document_id for hit in reply.passages}
    assert "limites-de-alcada.md" in retrieved


# ------------------------------------------------------------------ evaluation
def test_the_evaluation_splits_follow_ups_from_the_rest(index, dialogues):
    scores = run(index, dialogues, "concat", TemplateResponder())
    assert scores.turns > scores.follow_ups > 0
    assert 0.0 <= scores.follow_up_hit_rate <= 1.0


def test_every_dialogue_gets_its_own_customer_id(index, dialogues):
    """Sharing one would leak the unclear counter and escalate for the wrong reason.

    Asserted on the *reason*, not on the escalation count: a turn that legitimately
    retrieves nothing still escalates, and counting those would make this test fail for
    reasons that have nothing to do with customer ids.
    """
    scores = run(index, dialogues, "none", TemplateResponder())
    reasons = {r.escalation_reason for r in scores.records if r.escalated}
    assert "unclear_repeatedly" not in reasons, "the unclear counter leaked between dialogues"


def test_comparing_arms_scores_each(index, dialogues):
    table, results = compare(index, dialogues[:3], ["none", "concat"], TemplateResponder())
    assert list(table["rewriter"]) == ["none", "concat"]
    assert len(results) == 2


def test_the_report_shows_the_query_each_follow_up_used(index, dialogues, tmp_path):
    from collection.agents.conversation_eval import write_report

    table, results = compare(index, dialogues[:3], ["none", "concat"], TemplateResponder())
    report = tmp_path / "conversation-comparison.md"
    write_report(table, results, report)
    text = report.read_text(encoding="utf-8")
    assert "Follow-up turns" in text
    assert "query used" in text


def test_the_dialogue_set_labels_its_follow_ups(dialogues):
    assert dialogues
    assert any(turn.follow_up for dialogue in dialogues for turn in dialogue.turns)
    assert all(turn.relevant for dialogue in dialogues for turn in dialogue.turns)


# -------------------------------------------------------------- the context window
def test_a_zero_window_disables_inheritance(classifier, vocabulary):
    assert not resolve_intent(classifier.classify("sim"), "sim", thread(), vocabulary, 0).inherited


def test_four_turns_survive_one_uninformative_exchange(classifier, vocabulary):
    """Why the window is 4 and not 2.

    A courtesy turn between the question and the follow-up is skipped by `last_intent`,
    so a two-turn window — one exchange — has nothing left to find. This is the case the
    dialogue sweep cannot show, because the labelled set contains no courtesy turns.
    """
    conversation = (
        Conversation()
        .add("customer", "posso parcelar minha divida?", "negotiation")
        .add("agent", "Sobre as condições de parcelamento: ...")
        .add("customer", "ok obrigado", "small_talk")
        .add("agent", "Posso ajudar com mais alguma coisa?")
    )
    narrow = resolve_intent(classifier.classify("sim"), "sim", conversation, vocabulary, 2)
    default = resolve_intent(
        classifier.classify("sim"), "sim", conversation, vocabulary, CONTEXT_WINDOW
    )
    assert not narrow.inherited, "one exchange cannot see past a courtesy turn"
    assert default.intent is Intent.NEGOTIATION
    assert default.inherited


def test_the_window_bounds_how_far_back_an_intent_can_come_from():
    """An unbounded search would apply an intent from the start of a long thread."""
    conversation = Conversation().add("customer", "posso parcelar?", "negotiation")
    for _ in range(10):
        conversation.add("customer", "bom dia", "small_talk").add("agent", "Olá!")
    assert conversation.last_intent(CONTEXT_WINDOW) is None
    assert conversation.last_intent(999) is Intent.NEGOTIATION


def test_the_sweep_reports_the_cost_and_the_benefit(index, dialogues):
    from collection.agents.conversation_eval import sweep_window

    table = sweep_window(index, dialogues, (0, 2, 4), responder=TemplateResponder())
    rows = table.set_index("window")
    assert rows.loc[0, "intents_inherited"] == 0
    assert rows.loc[4, "intent_accuracy"] > rows.loc[0, "intent_accuracy"]
    # The cost side: a wider window always sends more to the model.
    assert rows.loc[4, "max_prompt_chars"] > rows.loc[2, "max_prompt_chars"]


def test_retrieval_is_unaffected_by_the_window(index, dialogues):
    from collection.agents.conversation_eval import sweep_window

    table = sweep_window(index, dialogues, (0, 4, 99), responder=TemplateResponder())
    assert table["hit_rate"].nunique() == 1, "the window must not touch retrieval"
