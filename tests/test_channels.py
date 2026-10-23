"""The channel contract, and the rule that keeps agents ignorant of it.

Channel-agnostic is only a property if something fails when it stops being true, so the
second test reads the agents package and names every file that reaches for a channel.
"""

from pathlib import Path

from collection.channels import Channel, ChannelCapabilities
from collection.channels.web import ChatChannel

AGENTS_DIR = Path(__file__).resolve().parent.parent / "src" / "collection" / "agents"


def test_a_channel_declares_what_it_can_carry():
    caps = ChannelCapabilities(
        accepts_audio=True, accepts_text=True, shows_delivery_status=True, realtime=False
    )
    assert caps.accepts_audio and not caps.realtime


def test_no_agent_or_orchestrator_imports_a_channel():
    """The enforceable form of channel-agnostic (spec section 5, decision 5)."""
    assert AGENTS_DIR.is_dir()
    offenders = sorted(
        str(path.relative_to(AGENTS_DIR))
        for path in AGENTS_DIR.rglob("*.py")
        if "collection.channels" in path.read_text()
    )
    assert offenders == [], f"agents must not import channels: {offenders}"


def test_the_chat_channel_satisfies_the_contract(index):
    channel = ChatChannel(index=index)
    assert isinstance(channel, Channel)
    assert channel.capabilities.accepts_audio
