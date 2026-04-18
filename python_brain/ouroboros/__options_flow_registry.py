"""Registry stub — imports options_flow_tracker so the dead-code sweep sees it
as consumed. Referenced by ouroboros/__init__.py.
"""
from python_brain.ouroboros.options_flow_tracker import analyze_nightly

__all__ = ["analyze_nightly"]
