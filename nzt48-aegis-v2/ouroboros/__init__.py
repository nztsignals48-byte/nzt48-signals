"""Ouroboros — Nightly analytics pipeline for AEGIS V2.

Runs after LSE close (23:45 ET), reads the day's WAL journal,
produces dynamic_weights.toml and universe_classification.toml
for next-day trading. Client ID = 200 (H41).

Quarantine rules:
  - NEVER writes to live WAL
  - NEVER influences live decisions in-session
  - Reads ONLY the finished day's journal
"""
