"""
pensieve-openai — OpenAI API memory provider
============================================

Wraps Pensieve's SQLite memory as OpenAI function-calling tools.
Compatible with gpt-4o, gpt-4-turbo, gpt-3.5-turbo, and any
model that supports OpenAI's function-calling / tools API.

Usage
-----
    from pensieve.openai import PensieveOpenAIProvider

    p = PensieveOpenAIProvider(user_id="alice")

    # Convenience: full memory-augmented chat
    reply = p.chat("What did we discuss last time?")

    # Or bring your own OpenAI client
    from openai import OpenAI
    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-4o",
        tools=p.tools(),
        messages=[{"role": "system", "content": p.system_prompt()},
                  {"role": "user", "content": "..."}],
    )
    if response.choices[0].finish_reason == "tool_calls":
        for tc in response.choices[0].message.tool_calls:
            import json
            result = p.handle_tool_call(tc.function.name, json.loads(tc.function.arguments))
"""

from .provider import PensieveOpenAIProvider

__all__ = ["PensieveOpenAIProvider"]
