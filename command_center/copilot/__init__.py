"""
command_center/copilot/__init__.py
===================================
NZT-48 Operator Copilot — READ-ONLY AI chatbot for the War Room.

Routes natural-language queries to deterministic handlers that inspect
system state, gate reports, drought diagnostics, and regime data.

SAFETY: This module CANNOT place orders. All "actions" in responses are
advisory suggestions only (e.g. "consider monitoring", "review setup").
"""

from command_center.copilot.router import CopilotRouter

__all__ = ["CopilotRouter"]
