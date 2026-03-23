# CRITICAL SYSTEM CONTRADICTIONS — MUST FIX

## CONTRADICTION 1: TypeA-F is dead code
- entry_engine.rs has 780 lines of TypeA-F detectors
- NONE are called from engine.rs in live/paper mode
- VanguardSniper is the only live signal source
- All TypeA-F config changes have ZERO effect

### OPTIONS:
A) Wire TypeA-F detectors into the live engine (Rust change)
B) Move TypeA-F detection into bridge.py (Python change)
C) Accept VanguardSniper as the only strategy and kill TypeA-F

### RECOMMENDED: Option B
- Add TypeA-F classification to bridge.py based on the indicators it already computes
- Bridge already has RSI, RVOL, Hurst, IBS, OBV — the same inputs TypeA-F need
- Set entry_type in the signal dict so Rust WAL records it for Ouroboros learning
- This is the fastest path to getting TypeA-F live without Rust recompilation

## CONTRADICTION 2: ISA core priority rotation
- Code prepends ISA core ETPs to watchlist when LSE is open
- User says: "no core ISAs anymore, we scan all tickers"
- Need to remove ISA core priority from rotation logic

## CONTRADICTION 3: Backtest tests strategies that don't run live
- institutional_backtest.py tests TypeA-F classification
- But live system uses VanguardSniper momentum scoring
- These are DIFFERENT signal generators
- Backtest results don't predict live performance

## NEXT STEPS:
1. Add TypeA-F classification to bridge.py (match entry_engine.rs logic)
2. Include entry_type in signal output to Rust
3. Remove ISA core priority from watchlist rotation
4. Rebuild + deploy
