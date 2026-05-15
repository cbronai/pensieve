"""
pensieve-crewai — CrewAI memory integration
============================================

Drop-in Memory replacement for CrewAI agents backed by Pensieve's
SQLite hierarchical store.

Usage
-----
    from pensieve.crewai import PensieveMemory
    from crewai import Agent, Task, Crew

    memory = PensieveMemory(user_id="my-crew")

    agent = Agent(
        role="Researcher",
        goal="Find and remember key facts",
        backstory="An expert researcher with persistent memory",
        memory=True,           # enable memory on the agent
    )

    # Inject Pensieve as the crew-level memory backend:
    crew = Crew(
        agents=[agent],
        tasks=[...],
        memory=True,
        memory_config={"provider": memory},
    )

    # Or use PensieveMemory directly on a CrewAI Memory instance:
    from crewai.memory.storage.interface import Storage
    agent_with_pensieve = Agent(
        role="Researcher",
        goal="...",
        backstory="...",
        memory=True,
        memory_config={"storage": memory.as_storage()},
    )
"""

from .memory import PensieveMemory

__all__ = ["PensieveMemory"]
