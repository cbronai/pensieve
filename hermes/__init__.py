"""
pensieve-memory — Hermes MemoryProvider plugin
==============================================

Drop-in persistent memory backend for Hermes Agent (NousResearch).
Uses SQLite + FTS5 for local hierarchical memory with automatic
conflict resolution.

Install
-------
Copy this directory to ~/.hermes/plugins/memory/pensieve/
or install via:
    hermes plugin add pensieve-memory

Config in ~/.hermes/config.yaml
-------------------------------
    memory:
      provider: pensieve
      memory_enabled: true
      user_profile_enabled: true
"""

from .provider import PensieveMemoryProvider

__all__ = ["PensieveMemoryProvider"]
