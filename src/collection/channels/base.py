"""The contract every channel implements.

The orchestrator and the agents are deliberately unaware of channels: an agent decides
*what* to say, the orchestrator decides *whether* anyone may be contacted, and neither
should know whether the words end up in a chat bubble or a synthesised voice. The moment
an agent imports a channel, that stops being true -- it starts branching on transport, and
every new channel means editing agent code. So the seam is made explicit here, and
`tests/test_channels.py` enforces it by scanning the agents package for channel imports.
A convention that nothing checks erodes the first time someone is in a hurry.

`Channel` is a `Protocol`, not an ABC, because a channel is defined by what it can do, not
by what it inherits. `ChatChannel` existed before this contract did and satisfies it
structurally; forcing a base class on it would add coupling for no gain.

`ChannelCapabilities` is how a channel states what it can carry. It is data rather than
`isinstance` checks scattered through callers, so a caller asks "can this channel take
audio?" and not "is this the voice channel?".
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ChannelCapabilities:
    """What a channel can carry and report back."""

    accepts_audio: bool
    accepts_text: bool
    #: Whether the channel can tell us a message was delivered or read.
    shows_delivery_status: bool
    #: Whether the customer is present and waiting, as opposed to reading later.
    realtime: bool


@runtime_checkable
class Channel(Protocol):
    """A way to reach a customer.

    `runtime_checkable` only verifies that the members exist, not their signatures, so it
    is a guard against forgetting one rather than a type check.
    """

    name: str
    capabilities: ChannelCapabilities

    def deliver(self, customer_id: str, text: str, **meta: object) -> str:
        """Send `text` to the customer and return a delivery id to track it by."""
        ...
