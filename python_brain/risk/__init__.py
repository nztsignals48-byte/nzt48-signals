"""Risk management layer — VaR/CVaR, stops, hedging, kill switches.

Public modules:
  realtime_var_cvar          — historical + parametric + MC VaR/CVaR
  marginal_var_attribution   — per-position VaR decomposition
  cvar_stop_placement        — expected-shortfall-budgeted stops
  tail_hedge_overlay         — regime-driven hedge recommendations
  hedge_executor             — auto-executes hedge recommendations
  stress_replay_weekly       — 2008/2020/2018 historical shock replays
  cross_portfolio_halt       — aggregate-DD kill switch
  portfolio_correlation_guard — max pairwise corr + cluster detection
"""
