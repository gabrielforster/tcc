"""Channel adapters: how a conversation reaches the orchestrator.

A channel owns transport and identity — receiving a message, knowing who sent it, holding
the thread together across turns — and nothing else. What to say is the agent's job, who
may be contacted is the orchestrator's. Keeping that line sharp is what makes the next
channel an adapter rather than a rewrite.
"""
