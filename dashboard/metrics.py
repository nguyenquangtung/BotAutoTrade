"""
Shared trading-metric definitions (stdlib only).

Single source of truth for:
  * net deal P&L  = profit + commission + swap + fee   (all in account currency)
  * win / loss / break-even classification of a closed deal
  * peak-to-trough drawdown (closed-deal running P&L or equity series)

Used by dashboard/analytics_db.py and reporting/reporter.py so the dashboard
and the e-mail reports always agree on what "net" means.
"""

from typing import Iterable, Optional, Tuple

# |net| at or below this (in account currency units; USC on the cent account)
# is counted as break-even rather than a win or loss.
BE_THRESHOLD = 0.05

# Account currencies that are "cent" accounts (1 unit = 0.01 of the base currency).
CENT_CURRENCIES = ("USC", "EUC")


def is_cent_currency(currency: Optional[str]) -> bool:
    """True for cent-account currencies (Exness 'USC', 'EUC')."""
    return (currency or "").strip().upper() in CENT_CURRENCIES


def deal_net(profit: float, commission: float = 0.0, swap: float = 0.0, fee: float = 0.0) -> float:
    """Net P&L of a deal: profit + commission + swap + fee (commission/fee are negative costs)."""
    return float(profit or 0.0) + float(commission or 0.0) + float(swap or 0.0) + float(fee or 0.0)


def deal_net_of(d) -> float:
    """deal_net() for an MT5 TradeDeal-like object (missing attrs count as 0)."""
    return deal_net(getattr(d, "profit", 0.0), getattr(d, "commission", 0.0),
                    getattr(d, "swap", 0.0), getattr(d, "fee", 0.0))


def classify(net: float, threshold: float = BE_THRESHOLD) -> str:
    """'win' | 'loss' | 'be' for a deal's net P&L."""
    if net > threshold:
        return "win"
    if net < -threshold:
        return "loss"
    return "be"


class DrawdownTracker:
    """Incremental peak-to-trough tracker. Feed values in time order."""

    def __init__(self, start_peak: Optional[float] = None):
        self.peak = start_peak
        self.max_dd = 0.0
        self.max_dd_pct = 0.0

    def add(self, value: float) -> None:
        if self.peak is None or value > self.peak:
            self.peak = value
            return
        dd = self.peak - value
        if dd > self.max_dd:
            self.max_dd = dd
        if self.peak > 0:
            pct = dd / self.peak * 100.0
            if pct > self.max_dd_pct:
                self.max_dd_pct = pct


def max_drawdown(values: Iterable[float], start_peak: Optional[float] = None) -> Tuple[float, float]:
    """(max_dd_abs, max_dd_pct_of_peak) for a series in time order."""
    t = DrawdownTracker(start_peak)
    for v in values:
        t.add(v)
    return t.max_dd, t.max_dd_pct
