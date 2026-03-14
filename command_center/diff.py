"""
command_center/diff.py
======================
"What changed since last tick" diff engine.

Compares consecutive EngineResult snapshots and emits a human-readable
diff for the Command Center "CHANGES" panel and Telegram alerts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from signal_engine.scoring import PlayScore


@dataclass
class TickDiff:
    tick_number:      int
    timestamp:        datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # New plays that appeared this tick
    new_plays:        list[PlayScore] = field(default_factory=list)
    # Plays that disappeared (ticker no longer in top list)
    dropped_plays:    list[str]       = field(default_factory=list)
    # Plays that changed star rating
    upgraded:         list[tuple[str, int, int]] = field(default_factory=list)  # (ticker, old, new)
    downgraded:       list[tuple[str, int, int]] = field(default_factory=list)

    # Regime change
    old_regime:       str = ""
    new_regime:       str = ""
    regime_changed:   bool = False

    # Health change
    health_changed:   bool = False
    old_health:       str = ""
    new_health:       str = ""

    # Signal drought appeared or cleared
    drought_appeared: bool = False
    drought_cleared:  bool = False

    @property
    def is_empty(self) -> bool:
        return (
            not self.new_plays and not self.dropped_plays and
            not self.upgraded and not self.downgraded and
            not self.regime_changed and not self.health_changed and
            not self.drought_appeared and not self.drought_cleared
        )

    def to_text(self) -> str:
        lines = [f"=== TICK #{self.tick_number} DIFF [{self.timestamp.strftime('%H:%M:%S')}] ==="]
        if self.is_empty:
            lines.append("  (no changes)")
            return "\n".join(lines)

        if self.regime_changed:
            lines.append(f"  REGIME: {self.old_regime} -> {self.new_regime}")
        if self.health_changed:
            lines.append(f"  DATA HEALTH: {self.old_health} -> {self.new_health}")
        if self.drought_appeared:
            lines.append("  *** SIGNAL DROUGHT ACTIVATED ***")
        if self.drought_cleared:
            lines.append("  *** SIGNAL DROUGHT CLEARED ***")
        for ps in self.new_plays:
            lines.append(f"  NEW  {ps.stars_str} {ps.ticker} {ps.direction} "
                         f"{ps.composite:.0f}/100 [{ps.label}]")
        for t, old, new in self.upgraded:
            old_str = "★" * old + "☆" * (5 - old)
            new_str = "★" * new + "☆" * (5 - new)
            lines.append(f"  UP   {t}: {old_str} -> {new_str}")
        for t, old, new in self.downgraded:
            old_str = "★" * old + "☆" * (5 - old)
            new_str = "★" * new + "☆" * (5 - new)
            lines.append(f"  DOWN {t}: {old_str} -> {new_str}")
        for t in self.dropped_plays:
            lines.append(f"  DROP {t}")
        return "\n".join(lines)

    # Minimum composite score to include a signal in Telegram alerts
    _MIN_TELEGRAM_SCORE = 55

    def to_telegram(self) -> Optional[str]:
        """Returns a Telegram-friendly alert or None if nothing notable.

        Only signals scoring >= 55/100 are sent. Qualified breakouts
        (composite >= 65 AND R:R >= 1.2) get the full BUY/SELL action format.
        """
        # Filter plays to only quality signals
        quality_plays = [ps for ps in self.new_plays if ps.composite >= self._MIN_TELEGRAM_SCORE]
        important = quality_plays or self.regime_changed or self.drought_appeared or self.drought_cleared
        if not important:
            return None
        parts = []
        if self.regime_changed:
            parts.append(f"Regime: {self.old_regime} \u2192 {self.new_regime}")
        if self.drought_appeared:
            parts.append("SIGNAL DROUGHT \u2014 no plays available")
        if self.drought_cleared and quality_plays:
            # Only announce drought cleared if we have quality signals to show
            parts.append("Signal drought cleared")
        for ps in quality_plays[:3]:
            rr = 0.0
            if ps.stop > 0 and ps.entry > 0:
                risk = abs(ps.entry - ps.stop)
                reward = abs(ps.target1 - ps.entry)
                rr = round(reward / risk, 1) if risk > 0 else 0.0
            is_qualified = ps.composite >= 65 and rr >= 1.2
            if is_qualified:
                action = "BUY" if ps.direction == "LONG" else "SELL"
                target_pct = abs(ps.target1 - ps.entry) / ps.entry * 100 if ps.entry > 0 else 0
                stars_filled = min(ps.stars, 5)
                stars_display = "\u2605" * stars_filled + "\u2606" * (5 - stars_filled)
                parts.append(
                    f"\U0001f514 {action} {ps.ticker} at \u00a3{ps.entry:.2f}\n"
                    f"STOP \u00a3{ps.stop:.2f} | TARGET \u00a3{ps.target1:.2f} (+{target_pct:.1f}%)\n"
                    f"R:R {rr}:1 | Score {ps.composite:.0f}/100 {stars_display}"
                )
            else:
                parts.append(f"{ps.stars_str} {ps.ticker} {ps.direction} "
                             f"{ps.composite:.0f}/100 entry={ps.entry:.2f} "
                             f"stop={ps.stop:.2f} T1={ps.target1:.2f}")
        return "\n".join(parts)


class DiffEngine:
    """Computes TickDiff between successive EngineResult snapshots."""

    def __init__(self) -> None:
        self._prev_plays:  dict[str, PlayScore] = {}
        self._prev_regime: str = ""
        self._prev_health: str = ""
        self._prev_drought: bool = False
        self._tick: int = 0

    def compute(self, result) -> TickDiff:
        self._tick += 1
        diff = TickDiff(tick_number=self._tick)

        current_plays = {p.ticker: p for p in result.plays}

        # Regime change
        new_regime = result.regime
        if self._prev_regime and new_regime != self._prev_regime:
            diff.regime_changed = True
            diff.old_regime = self._prev_regime
            diff.new_regime = new_regime

        # Health change
        hs = result.health_summary
        new_health = getattr(hs, "status", "") if hs else ""
        if self._prev_health and new_health != self._prev_health:
            diff.health_changed = True
            diff.old_health = self._prev_health
            diff.new_health = new_health

        # New plays
        diff.new_plays = [
            p for t, p in current_plays.items()
            if t not in self._prev_plays
        ]

        # Dropped plays
        diff.dropped_plays = [
            t for t in self._prev_plays
            if t not in current_plays
        ]

        # Star rating changes
        for t, ps in current_plays.items():
            if t in self._prev_plays:
                old_stars = self._prev_plays[t].stars
                new_stars = ps.stars
                if new_stars > old_stars:
                    diff.upgraded.append((t, old_stars, new_stars))
                elif new_stars < old_stars:
                    diff.downgraded.append((t, old_stars, new_stars))

        # Drought state
        is_drought = result.drought is not None
        if is_drought and not self._prev_drought:
            diff.drought_appeared = True
        if not is_drought and self._prev_drought:
            diff.drought_cleared = True

        # Update state
        self._prev_plays  = current_plays
        self._prev_regime = new_regime
        self._prev_health = new_health
        self._prev_drought = is_drought

        return diff
