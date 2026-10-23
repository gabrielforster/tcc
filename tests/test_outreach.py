"""Outbound composition: the offer, the message, and what may not be in it.

The theme here is the inverse of the responsive tests. Inbound, the risk is answering
badly a question somebody asked. Outbound, the risk is writing at all -- to someone who
did not ask, about money, under a statute that regulates exactly that. So most of these
tests are about what a message must not contain, and the sharpest one is the property in
`test_the_template_arm_is_compliant_on_every_labelled_brief`: the control arm fills slots,
so it cannot invent a figure or pick a threatening word, and a failure there means the
checker is wrong before the composer is.
"""

from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from collection.agents.events import Event, EventType
from collection.agents.proactive import CHANNEL_BY_STAGE, ProactiveAgent
from collection.channels import DELIVERY_CHANNELS
from collection.outreach.brief import MIN_INSTALMENT_BRL, Brief, load_briefs, load_terms
from collection.outreach.compliance import LENGTH_CAP, check
from collection.outreach.compose import (
    AUTOMATED_MARKER,
    OPT_OUT_TOKEN,
    Composer,
    LLMComposer,
    OutboundMessage,
    TemplateComposer,
    build_user_prompt,
    get_composer,
)
from collection.outreach.evaluate import distinctness, run


@pytest.fixture(scope="module")
def briefs() -> list[Brief]:
    return load_briefs()


@pytest.fixture
def brief() -> Brief:
    return Brief(
        customer_id="cus_t",
        customer_name="Mariana Souza",
        amount=1250.0,
        days_late=18,
        channel="chat",
    )


# ------------------------------------------------------------------ the offer


def test_terms_come_from_the_instalment_table_not_from_python() -> None:
    """The offer and the FAQ answer must not be able to drift apart."""
    terms = load_terms()
    assert terms["8-30"].max_instalments == 6
    assert terms["8-30"].discount_percent == 10
    assert terms["60+"].faixa == "61 a 180 dias"


def test_pre_due_has_no_offer_to_make() -> None:
    assert Brief(customer_id="c", amount=100.0, days_late=-2).terms is None


def test_the_instalment_floor_binds_before_the_table_does() -> None:
    """R$ 230 over twelve instalments would be R$ 19 each, which cannot be offered."""
    small = Brief(customer_id="c", amount=230.0, days_late=120)
    assert small.terms is not None
    assert small.terms.max_instalments == 12
    assert small.instalments == 4
    assert 230.0 / small.instalments >= MIN_INSTALMENT_BRL


# ------------------------------------------------------- the template control


def test_the_template_arm_is_compliant_on_every_labelled_brief(briefs: list[Brief]) -> None:
    composer = TemplateComposer()
    for item in briefs:
        result = check(composer.compose(item), item)
        assert result.compliant, f"{item.customer_id}: {result.violations}"


def test_every_message_names_the_creditor_says_it_is_automated_and_offers_the_exit(
    brief: Brief,
) -> None:
    text = TemplateComposer().compose(brief).text
    assert brief.creditor in text
    assert AUTOMATED_MARKER in text
    assert OPT_OUT_TOKEN in text


def test_a_missing_name_degrades_the_greeting_instead_of_inventing_one() -> None:
    anonymous = Brief(customer_id="c", amount=500.0, days_late=20)
    text = TemplateComposer().compose(anonymous).text
    assert text.startswith("Olá, aqui é a")


def test_sms_gets_the_short_template_and_fits_one_segment() -> None:
    """The full templates run to ~250 characters; an SMS segment is 160."""
    short = Brief(customer_id="c", customer_name="Rafael", amount=120.0, days_late=6, channel="sms")
    long = Brief(customer_id="c", customer_name="Rafael", amount=120.0, days_late=6)
    composer = TemplateComposer()
    sms = composer.compose(short)
    whatsapp = composer.compose(long)
    assert len(sms.text) <= LENGTH_CAP["sms"]
    assert len(whatsapp.text) > LENGTH_CAP["sms"]
    # The cap takes the greeting first: the three disclosures are not negotiable.
    assert AUTOMATED_MARKER in sms.text and OPT_OUT_TOKEN in sms.text
    assert "Rafael" not in sms.text


def test_no_message_exceeds_its_own_channel_cap(briefs: list[Brief]) -> None:
    composer = TemplateComposer()
    for item in briefs:
        message = composer.compose(item)
        assert len(message.text) <= LENGTH_CAP[item.channel], item.customer_id


# --------------------------------------------------------------- the checker


def test_an_invented_figure_is_caught(brief: Brief) -> None:
    text = (
        f"Olá, aqui é a {brief.creditor} ({AUTOMATED_MARKER}). Seu débito de R$ 1.250,00 "
        f"já acumulou R$ 97,30 de juros. Responda {OPT_OUT_TOKEN}."
    )
    result = check(text, brief)
    assert not result.compliant
    assert result.invented_numbers == ["97.3"]


def test_the_brief_s_own_figures_are_not_inventions(brief: Brief) -> None:
    text = (
        f"Olá, aqui é a {brief.creditor} ({AUTOMATED_MARKER}). Seu débito de R$ 1.250,00 "
        f"está com 18 dias de atraso. Responda {OPT_OUT_TOKEN}."
    )
    assert check(text, brief).compliant


def test_threatening_vocabulary_is_caught_with_or_without_accents(brief: Brief) -> None:
    accented = check(f"Vamos protocolar uma ação judicial. {OPT_OUT_TOKEN}", brief)
    plain = check(f"Vamos protocolar uma acao judicial. {OPT_OUT_TOKEN}", brief)
    assert "acao judicial" in accented.forbidden_terms
    assert accented.forbidden_terms == plain.forbidden_terms


def test_a_message_without_the_opt_out_is_not_sendable(brief: Brief) -> None:
    text = f"Olá, aqui é a {brief.creditor} ({AUTOMATED_MARKER}). Seu débito está em aberto."
    result = check(text, brief)
    assert result.missing_disclosures == ["opt_out"]
    assert not result.sendable


def test_an_offer_beyond_the_stage_is_caught_even_when_the_digits_are_legitimate(
    brief: Brief,
) -> None:
    """`1,5` is in the brief as an interest rate; it is not a discount this stage allows."""
    over = check(f"Pague com 20% de desconto em até 24x. {OPT_OUT_TOKEN}", brief)
    assert "discount 20% > 10%" in over.over_authority
    assert "instalments 24 > 6" in over.over_authority


def test_length_is_a_flaw_but_not_a_legal_one(brief: Brief) -> None:
    """A long message is bad service; it is not a reason to refuse to send it."""
    composed = TemplateComposer().compose(brief)
    long = OutboundMessage(
        text=composed.text + " " + "palavra " * 60,
        composer="test",
        stage=brief.stage,
        channel=brief.channel,
    )
    result = check(long, brief)
    assert result.too_long
    assert not result.compliant
    assert result.sendable


# -------------------------------------------------------------- the LLM arm


class _Boom:
    name = "boom"
    model = "boom"

    def complete(self, system: str, user: str):
        from collection.llm.client import LLMError

        raise LLMError("no key")


def test_a_generation_failure_falls_back_to_the_template_and_says_so(brief: Brief) -> None:
    composer = LLMComposer(client=_Boom())
    message = composer.compose(brief)
    assert message.composer == "llm:fallback"
    assert message.text == TemplateComposer().compose(brief).text
    assert composer.failures


def test_the_model_is_handed_the_brief_and_nothing_else(brief: Brief) -> None:
    from collection.llm.client import ScriptedClient

    client = ScriptedClient(replies=["ok"])
    LLMComposer(client=client).compose(brief)
    _, user = client.calls[0]
    assert "FATOS:" in user
    assert "R$ 1.250,00" in user
    assert "8 a 30 dias" in user
    # Nothing about other customers, other invoices or the knowledge base.
    assert "cus_" not in user


def test_the_prompt_carries_the_capped_instalment_count_not_the_table_s() -> None:
    small = Brief(customer_id="c", amount=230.0, days_late=120)
    assert "parcelas_maximas: 4" in build_user_prompt(small)


def test_an_empty_completion_falls_back_rather_than_sending_nothing(brief: Brief) -> None:
    from collection.llm.client import ScriptedClient

    message = LLMComposer(client=ScriptedClient(replies=["   "])).compose(brief)
    assert message.composer == "llm:fallback"
    assert message.text


# ------------------------------------------------------------- the agent gate


class _Reckless(Composer):
    name = "reckless"

    def compose(self, brief: Brief) -> OutboundMessage:
        return OutboundMessage(
            text="Pague hoje ou seu nome vai para o Serasa.",
            composer=self.name,
            stage=brief.stage,
            channel=brief.channel,
        )


#: A weekday morning inside the permitted contact window, so the rules engine allows the
#: attempt and these tests are about composition rather than about the clock.
BUSINESS_HOURS = datetime(2026, 9, 10, 10, 30, tzinfo=ZoneInfo("America/Sao_Paulo"))


def _agent(**kwargs) -> ProactiveAgent:
    return ProactiveAgent(clock=lambda: BUSINESS_HOURS, **kwargs)


def _overdue() -> Event:
    return Event(
        type=EventType.OVERDUE_DETECTED,
        customer_id="cus_a",
        invoice_id="inv_1",
        payload={"days_late": 18, "amount": 1250.0, "customer_name": "Mariana Souza"},
    )


def test_a_scheduled_contact_carries_the_text_that_would_be_sent() -> None:
    produced = _agent().handle(_overdue())
    assert len(produced) == 1
    event = produced[0]
    assert event.type == EventType.CONTACT_SCHEDULED
    assert event.payload["composer"] == "template"
    assert "Mariana" in event.payload["message"]
    assert event.payload["message_chars"] == len(event.payload["message"])


def test_a_non_compliant_message_is_escalated_instead_of_scheduled() -> None:
    produced = _agent(composer=_Reckless()).handle(_overdue())
    assert len(produced) == 1
    event = produced[0]
    assert event.type == EventType.ESCALATED_TO_HUMAN
    assert event.payload["reason"] == "message_not_compliant"
    assert "serasa" in event.payload["detail"]


def test_the_message_is_written_only_after_the_rules_allow_the_contact() -> None:
    """A suppressed contact must not even compose: the cheapest way to not send it."""
    from collection.rules.policy import ContactHistory

    agent = _agent(
        history=lambda customer_id, invoice_id: ContactHistory(opted_out=True),
        composer=_Reckless(),
    )
    produced = agent.handle(_overdue())
    assert [event.type for event in produced] == [EventType.SUPPRESSED]


# ------------------------------------------------------------- the comparison


def test_identical_messages_score_zero_distinctness() -> None:
    scores = run(
        [
            Brief(customer_id="a", amount=1000.0, days_late=20),
            Brief(customer_id="b", amount=1000.0, days_late=20),
        ],
        composer=TemplateComposer(),
    )
    assert scores.distinct_pairs == 1
    assert scores.distinctness == pytest.approx(0.0, abs=1e-9)


def test_the_comparison_reports_a_perfect_control_arm(briefs: list[Brief]) -> None:
    scores = run(briefs, composer="template")
    assert scores.compliance_rate == 1.0
    assert scores.invented_rate == 0.0
    assert scores.over_cap == 0
    assert scores.distinct_pairs > 0


def test_distinctness_ignores_messages_that_share_no_stage_and_channel() -> None:
    records = run(
        [
            Brief(customer_id="a", amount=1000.0, days_late=4),
            Brief(customer_id="b", amount=1000.0, days_late=90, channel="sms"),
        ],
        composer="template",
    ).records
    assert distinctness(records) == (0.0, 0)


def test_an_unknown_composer_is_refused() -> None:
    with pytest.raises(ValueError, match="Unknown composer"):
        get_composer("telepathy")


def test_stages_route_to_delivery_channels_that_exist() -> None:
    assert set(CHANNEL_BY_STAGE.values()) <= DELIVERY_CHANNELS
    assert set(LENGTH_CAP) == DELIVERY_CHANNELS


def test_an_unknown_channel_does_not_crash_composition() -> None:
    """The fallback falls back to a key that exists, not to a retired name."""
    brief = replace(load_briefs()[0], channel="carrier_pigeon")
    result = check(TemplateComposer().compose(brief).text, brief)
    assert result.length_cap == LENGTH_CAP["chat"]
