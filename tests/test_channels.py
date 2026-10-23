"""The channel contract, and the rule that keeps agents ignorant of it.

Channel-agnostic is only a property if something fails when it stops being true, so the
second test reads the agents package and names every file that reaches for a channel.
"""

import ast
from pathlib import Path

import pytest

from collection.agents.responsive import TemplateResponder
from collection.channels import Channel, ChannelCapabilities
from collection.channels.delivery import BackwardsStatus, DeliveryLog, DeliveryStatus
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
