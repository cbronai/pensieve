"""
pensieve-pydantic-ai — Pydantic AI memory tools
================================================

Registers Pensieve memory tools on any Pydantic AI agent.

Usage
-----
    from pydantic_ai import Agent
    from pensieve.pydantic_ai import add_pensieve_tools, PensieveDeps

    agent = Agent(
        'claude-opus-4-6',
        deps_type=PensieveDeps,
        system_prompt="You are a helpful assistant with persistent memory.",
    )
    add_pensieve_tools(agent)

    result = agent.run_sync(
        "What's my favourite colour?",
        deps=PensieveDeps(user_id="alice"),
    )
    print(result.data)

    # Or create a pre-wired agent in one call:
    from pensieve.pydantic_ai import create_pensieve_agent

    agent = create_pensieve_agent(model='claude-opus-4-6', user_id='alice')
    result = agent.run_sync("Remember that I prefer dark mode.")
"""

from .tools import PensieveDeps, add_pensieve_tools, create_pensieve_agent

__all__ = ["PensieveDeps", "add_pensieve_tools", "create_pensieve_agent"]
