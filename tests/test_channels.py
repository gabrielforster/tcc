"""The channel contract, and the rule that keeps agents ignorant of it.

Channel-agnostic is only a property if something fails when it stops being true, so the
second test reads the agents package and names every file that reaches for a channel.
"""

import ast
from pathlib import Path

import pytest

from collection.agents.events import Event, EventType
from collection.agents.responsive import TemplateResponder
from collection.channels import Channel, ChannelCapabilities
from collection.channels import session as session_module
from collection.channels.delivery import BackwardsStatus, DeliveryLog, DeliveryStatus
from collection.channels.session import SessionNotFound, issue_token
from collection.channels.web import ChatChannel

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
AGENTS_DIR = SRC_DIR / "collection" / "agents"
CHANNELS = "collection.channels"
DISPUTE = "esse valor ta errado, nao e isso que eu devo"


def _into_channels(module: str) -> bool:
    return module == CHANNELS or module.startswith(CHANNELS + ".")


def channel_imports(path: Path, src_root: Path = SRC_DIR) -> list[str]:
    """The channel modules a source file imports, as absolute dotted names.

    Parsed rather than grepped: a substring match misses `from collection import channels`,
    relative imports and `importlib`, and fires on comments and docstrings. Kept as a
    function of a path so the self-test can aim it at a file known to offend.
    """
    package = list(path.relative_to(src_root).with_suffix("").parts)[:-1]
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if _into_channels(a.name)]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            if _into_channels(module):
                found.append(module)
            else:
                # `from collection import channels` names the module in the alias list.
                found += [
                    f"{module}.{a.name}" for a in node.names if _into_channels(f"{module}.{a.name}")
                ]
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            arg = node.args[0]
            if (
                name in {"import_module", "__import__"}
                and isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and _into_channels(arg.value)
            ):
                found.append(arg.value)
    return found


def test_a_channel_declares_what_it_can_carry():
    caps = ChannelCapabilities(
        accepts_audio=True, accepts_text=True, shows_delivery_status=True, realtime=False
    )
    assert caps.accepts_audio and not caps.realtime


def test_no_agent_or_orchestrator_imports_a_channel():
    """The enforceable form of channel-agnostic (spec section 5, decision 5)."""
    files = sorted(AGENTS_DIR.rglob("*.py"))
    assert files, "the scan found no agent files, so it would pass vacuously"
    offenders = {str(p.relative_to(AGENTS_DIR)): channel_imports(p) for p in files}
    offenders = {name: found for name, found in offenders.items() if found}
    assert offenders == {}, f"agents must not import channels: {offenders}"


@pytest.mark.parametrize(
    "source",
    [
        "import collection.channels.web",
        "from collection.channels import base",
        "from collection.channels.web import ChatChannel",
        "from collection import channels",
        "from ..channels import base",
        "from .. import channels",
        "import importlib\nimportlib.import_module('collection.channels.web')",
    ],
)
def test_the_scanner_flags_every_way_of_importing_a_channel(tmp_path, source):
    """Without this, a scanner that silently matched nothing would pass forever."""
    agent = tmp_path / "collection" / "agents" / "offender.py"
    agent.parent.mkdir(parents=True)
    agent.write_text(source + "\n")
    assert channel_imports(agent, src_root=tmp_path)


def test_the_scanner_ignores_prose_and_unrelated_imports(tmp_path):
    agent = tmp_path / "collection" / "agents" / "clean.py"
    agent.parent.mkdir(parents=True)
    agent.write_text(
        '"""Mentions collection.channels in prose."""\n'
        "# see collection.channels\n"
        "from collection.rag import index\n"
        "from . import events\n"
    )
    assert channel_imports(agent, src_root=tmp_path) == []


def test_the_chat_channel_satisfies_the_contract(index):
    channel = ChatChannel(index=index)
    assert isinstance(channel, Channel)
    assert channel.capabilities.accepts_audio


# ---------------------------------------------------------------- delivery status
def test_status_only_moves_forward():
    log = DeliveryLog()
    rec = log.record("oi")
    assert rec.status is DeliveryStatus.ENVIADA
    log.advance(rec.id, DeliveryStatus.LIDA)
    with pytest.raises(ValueError) as caught:
        log.advance(rec.id, DeliveryStatus.ENTREGUE)
    assert isinstance(caught.value, BackwardsStatus)
    assert caught.value.current is DeliveryStatus.LIDA
    assert caught.value.attempted is DeliveryStatus.ENTREGUE
    assert log.of(rec.id).status is DeliveryStatus.LIDA


def test_repeating_a_status_is_a_noop_and_rungs_may_be_skipped():
    log = DeliveryLog()
    rec = log.record("oi")
    first = log.advance(rec.id, DeliveryStatus.LIDA)
    assert log.advance(rec.id, DeliveryStatus.LIDA) == first


def test_an_unknown_delivery_id_raises_instead_of_creating_a_record():
    log = DeliveryLog()
    with pytest.raises(KeyError):
        log.advance("dlv_nope", DeliveryStatus.LIDA)
    with pytest.raises(KeyError):
        log.of("dlv_nope")


def test_a_customer_turn_marks_the_previous_message_answered(index):
    channel = ChatChannel(index=index, responder=TemplateResponder())
    session, _ = channel.open()
    first = channel.send(session.session_id, "quais os juros por atraso?")
    assert channel.delivery.of(first.delivery_id).status is DeliveryStatus.ENVIADA
    channel.send(session.session_id, "e em 6 vezes?")
    assert channel.delivery.of(first.delivery_id).status is DeliveryStatus.RESPONDIDA


def test_an_escalated_thread_stops_advancing(index):
    """Review Focus 5: handover ends the thread, so nothing becomes respondida."""
    channel = ChatChannel(index=index, responder=TemplateResponder())
    session, _ = channel.open()
    handover = channel.send(session.session_id, DISPUTE)
    assert handover.escalated
    assert channel.delivery.of(handover.delivery_id).status is DeliveryStatus.ENVIADA
    # The widget stops accepting input, but a late request still reaches the channel.
    channel.send(session.session_id, "alguém aí?")
    assert channel.delivery.of(handover.delivery_id).status is DeliveryStatus.ENVIADA


def test_deliver_records_an_outbound_message(index):
    channel = ChatChannel(index=index)
    delivery_id = channel.deliver("cust_1", "Sua parcela vence amanhã.")
    assert channel.delivery.of(delivery_id).status is DeliveryStatus.ENVIADA
    assert channel.capabilities.shows_delivery_status
    assert not channel.capabilities.realtime


def test_the_greeting_is_trackable(index):
    channel = ChatChannel(index=index)
    session, _ = channel.open()
    assert channel.delivery.of(session.greeting_delivery_id).status is DeliveryStatus.ENVIADA


# ------------------------------------------------------------------- seeded threads
SCHEDULED_TEXT = "Olá, Marina, sua parcela vence amanhã. Posso ajudar com a segunda via?"


def contact_scheduled(customer_id: str = "c1", text: str = SCHEDULED_TEXT, **payload):
    """The event `ProactiveAgent` emits: the text rides in `payload["message"]`."""
    return Event(
        type=EventType.CONTACT_SCHEDULED,
        customer_id=customer_id,
        invoice_id="inv_1",
        payload={"channel": "chat", "message": text, **payload},
    )


def test_a_scheduled_contact_is_waiting_when_the_customer_opens_the_widget(index):
    channel = ChatChannel(index=index)
    delivery_id = channel.seed(contact_scheduled())
    session, _ = channel.open(token=issue_token("c1"))
    transcript = channel.transcript(session.session_id)
    assert "Marina" in transcript[-1]["text"]
    assert transcript[-1]["role"] == "agent"
    assert transcript[0]["text"] != transcript[-1]["text"], "greeting comes first"
    assert channel.delivery.of(delivery_id).status is DeliveryStatus.ENVIADA
    # Drained: a second thread does not repeat it.
    again, _ = channel.open(token=issue_token("c1"))
    assert len(channel.transcript(again.session_id)) == 1


def test_a_seeded_message_is_not_shown_to_another_customer(index):
    channel = ChatChannel(index=index)
    channel.seed(contact_scheduled("c1"))
    session, _ = channel.open(token=issue_token("c2"))
    assert len(channel.transcript(session.session_id)) == 1


def test_seeding_emits_contact_sent_linked_to_the_schedule(index):
    channel = ChatChannel(index=index)
    scheduled = contact_scheduled()
    delivery_id = channel.seed(scheduled)
    sent = [e for e in channel.audit if e.type is EventType.CONTACT_SENT]
    assert len(sent) == 1
    assert sent[0].caused_by == scheduled.event_id
    assert sent[0].payload["delivery_id"] == delivery_id


def test_a_reply_to_a_seeded_message_marks_it_answered(index):
    channel = ChatChannel(index=index, responder=TemplateResponder())
    delivery_id = channel.seed(contact_scheduled())
    session, _ = channel.open(token=issue_token("c1"))
    channel.send(session.session_id, "quais os juros por atraso?")
    assert channel.delivery.of(delivery_id).status is DeliveryStatus.RESPONDIDA


def test_an_opted_out_customer_is_never_seeded(index):
    """Review Focus 2. Suppression is the orchestrator's; the channel only supplies the set."""
    channel = ChatChannel(index=index)
    channel.opted_out.add("c1")
    assert channel.seed(contact_scheduled("c1")) is None
    assert [e.type for e in channel.audit] == [EventType.CONTACT_SCHEDULED]
    assert not [e for e in channel.audit if e.type is EventType.CONTACT_SENT]
    session, _ = channel.open(token=issue_token("c1"))
    assert len(channel.transcript(session.session_id)) == 1


def test_opting_out_after_a_seed_discards_the_waiting_message(index):
    channel = ChatChannel(index=index, responder=TemplateResponder())
    channel.seed(contact_scheduled("c1"))
    other, _ = channel.open(token=issue_token("c1"))  # drains it; seed again for the real case
    channel.seed(contact_scheduled("c1"))
    channel.send(other.session_id, "parem de me mandar mensagem")
    assert "c1" in channel.opted_out
    fresh, _ = channel.open(token=issue_token("c1"))
    assert len(channel.transcript(fresh.session_id)) == 1


def test_a_seed_whose_session_expired_is_still_waiting_on_the_next_open(index, monkeypatch):
    """Review Focus 4: the seed outlives the session, because it is not one."""
    channel = ChatChannel(index=index)
    first, _ = channel.open(token=issue_token("c1"))
    channel.seed(contact_scheduled())
    later = session_module.time.time() + session_module.SESSION_TTL_SECONDS + 60
    with monkeypatch.context() as expired:
        expired.setattr(session_module.time, "time", lambda: later)
        with pytest.raises(SessionNotFound):
            channel.store.get(first.session_id)
    session, _ = channel.open(token=issue_token("c1"))
    assert "Marina" in channel.transcript(session.session_id)[-1]["text"]


def test_opting_out_by_any_path_after_a_seed_stops_delivery_on_open(index):
    channel = ChatChannel(index=index)
    channel.seed(contact_scheduled("c1"))
    channel.opted_out.add("c1")  # not via send(): e.g. recorded by another channel
    session, _ = channel.open(token=issue_token("c1"))
    assert len(channel.transcript(session.session_id)) == 1


def test_every_seeded_message_is_answered_by_the_next_turn(index):
    channel = ChatChannel(index=index, responder=TemplateResponder())
    first = channel.seed(contact_scheduled(text="Primeiro aviso."))
    second = channel.seed(contact_scheduled(text="Segundo aviso."))
    session, _ = channel.open(token=issue_token("c1"))
    channel.send(session.session_id, "quais os juros por atraso?")
    for delivery_id in (first, second, session.greeting_delivery_id):
        assert channel.delivery.of(delivery_id).status is DeliveryStatus.RESPONDIDA


def test_a_delivered_contact_is_not_recorded_as_a_skip(index):
    channel = ChatChannel(index=index)
    assert channel.seed(contact_scheduled("c1")) is not None
    assert channel.skipped == []
    assert EventType.CONTACT_SENT in [e.type for e in channel.audit]


def test_a_wrong_channel_contact_is_in_the_audit_and_in_skipped(index):
    channel = ChatChannel(index=index)
    scheduled = contact_scheduled("c1", channel="voice")
    channel.seed(scheduled)
    assert scheduled in channel.audit
    assert channel.skipped == [("other_channel", scheduled)]


def test_skips_are_distinguishable_and_empty_text_is_loud(index):
    channel = ChatChannel(index=index)
    channel.opted_out.add("c2")
    assert channel.seed(contact_scheduled("c2")) is None
    assert channel.seed(contact_scheduled("c1", channel="voice")) is None
    assert [reason for reason, _ in channel.skipped] == ["suppressed", "other_channel"]
    with pytest.raises(ValueError, match="no message text"):
        channel.seed(contact_scheduled(text="  "))
    session, _ = channel.open(token=issue_token("c1"))
    assert len(channel.transcript(session.session_id)) == 1


# ---------------------------------------------------------------- the seeding job
# The proactive agent and the mailbox existed; nothing joined them, so the behaviour the
# demo claims lived only in the tests above. These cover the join.


def _seeded_job(index):
    from collection.channels.outbound import OPTED_OUT
    from collection.channels.outbound import run as seed_contacts

    channel = ChatChannel(index=index, opted_out=set(OPTED_OUT))
    return channel, seed_contacts(channel=channel)


def test_the_job_leaves_a_scheduled_message_waiting_for_the_customer(index):
    channel, result = _seeded_job(index)
    customers = [event.customer_id for event, _ in result.seeded]
    assert "cus_a" in customers
    session, _ = channel.open(token=issue_token("cus_a"))
    transcript = channel.transcript(session.session_id)
    assert len(transcript) == 2, "the greeting, then the scheduled contact"
    assert transcript[-1]["role"] == "agent"
    assert "Marina" in transcript[-1]["text"]


def test_the_job_declines_a_contact_scheduled_for_another_channel(index):
    channel, result = _seeded_job(index)
    assert [event.customer_id for event in result.other_channel] == ["cus_c"]
    session, _ = channel.open(token=issue_token("cus_c"))
    assert len(channel.transcript(session.session_id)) == 1, "greeting only"


def test_the_job_never_seeds_an_opted_out_customer(index):
    channel, result = _seeded_job(index)
    assert "cus_optout" in [event.customer_id for event in result.suppressed]
    assert "cus_optout" not in [event.customer_id for event, _ in result.seeded]
    session, _ = channel.open(token=issue_token("cus_optout"))
    assert len(channel.transcript(session.session_id)) == 1, "greeting only"


def test_every_detected_receivable_is_accounted_for(index):
    """No contact may fall between the outcomes: a silent drop is the failure to fear."""
    from collection.channels.outbound import overdue

    _, result = _seeded_job(index)
    assert result.total == len(overdue())


def test_a_seeded_message_is_tracked_from_the_moment_it_is_scheduled(index):
    channel, result = _seeded_job(index)
    event, delivery_id = result.seeded[0]
    assert channel.delivery.of(delivery_id).status is DeliveryStatus.ENVIADA
    assert event.type is EventType.CONTACT_SCHEDULED
