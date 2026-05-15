"""
pensieve-claude — Claude API memory provider
============================================

Wraps Pensieve's SQLite-backed hierarchical memory as native Claude tool calls.
Compatible with claude-opus-4-6, claude-sonnet-4-6, claude-haiku-4-5.

Usage
-----
    from pensieve.claude import PensieveClaudeProvider

    p = PensieveClaudeProvider(user_id="alice")

    # Convenience: full memory-augmented chat
    reply = p.chat("What's my favourite colour?")

    # Or bring your own Anthropic client
    import anthropic
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        system=p.system_prompt(),
        tools=p.tools(),
        messages=[{"role": "user", "content": "..."}],
    )
    if response.stop_reason == "tool_use":
        for block in response.content:
            if block.type == "tool_use":
                result = p.handle_tool_call(block.name, block.input)
"""

from .provider import PensieveClaudeProvider

__all__ = ["PensieveClaudeProvider"]
