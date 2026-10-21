"""Outbound message composition: what the proactive agent actually says."""

from collection.outreach.brief import Brief, Terms, load_briefs, load_terms
from collection.outreach.compliance import Compliance, check
from collection.outreach.compose import (
    Composer,
    LLMComposer,
    OutboundMessage,
    TemplateComposer,
    get_composer,
)

__all__ = [
    "Brief",
    "Compliance",
    "Composer",
    "LLMComposer",
    "OutboundMessage",
    "TemplateComposer",
    "Terms",
    "check",
    "get_composer",
    "load_briefs",
    "load_terms",
]
