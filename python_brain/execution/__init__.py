"""Execution layer — order routing, impact models, venue selection, paper haircuts.

Public modules:
  almgren_chriss_executor  — optimal execution slicing
  impact_aware_router      — venue + order-type selection
  paper_haircut            — per-venue live-vs-paper bps
  ioc_fok_support          — IOC/FOK translation
  venue_slippage_tracker   — implementation shortfall per venue
  account_size_aware_ac    — Almgren-Chriss scaled to account size
"""
