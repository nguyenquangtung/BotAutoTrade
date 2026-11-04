"""
Reads the Nyao Scalper EA's persisted state files (MQL5/Files/NyaoScalper/state_<login>_<symbol>_<magic>.bin)
so the dashboard can show WHY the bot is idle: loss-streak pause, paper trade, cooldowns.

The header layout mirrors `struct PersistHeader` in nyao_scalper.mq5 (MQL5 packs structs with 1-byte
alignment). Only STATE_FILE_VERSION 4 is decoded; any other version is reported as unknown instead of
being misread. The EA rewrites the file whenever this state changes, so it is current while the EA runs.
"""

import glob
import os
import re
import struct
from datetime import datetime, timezone

from analytics_db import TZ_VN

# version, recordSize, count, login, magic, peakEquity, lastPeakEquity, minEquityTriggerCount,
# consecutiveLossCount, cooldownUntilBarTime, equityPauseUntil, basketPauseUntil, streakLosses,
# streakPaused, paperDir, paperEntry, paperTarget, paperStop, initialLeverage, initialBalance,
# savedAt, lastBalanceDeal
HEADER_V4 = struct.Struct("<iiiqqddiiqqqi?idddqdqQ")
FILE_RE = re.compile(r"^state_(\d+)_(.+)_(\d+)\.bin$")


def _vn(epoch):
    """MQL datetime (Exness server time = GMT+0) -> 'YYYY-MM-DD HH:MM:SS' in Vietnam time, or None."""
    if not epoch or epoch <= 0:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(TZ_VN).strftime("%Y-%m-%d %H:%M:%S")


def _decode(path, symbol, magic):
    with open(path, "rb") as f:
        raw = f.read(HEADER_V4.size)
    version = struct.unpack_from("<i", raw)[0] if len(raw) >= 4 else None
    out = {"symbol": symbol, "magic": magic, "version": version, "ok": False}
    if version != 4 or len(raw) < HEADER_V4.size:
        return out
    (_, _, count, _, _, peak_eq, _, _, consec_loss, cooldown_bar, eq_pause, basket_pause,
     streak_losses, streak_paused, paper_dir, paper_entry, paper_target, paper_stop,
     leverage, initial_balance, saved_at, _) = HEADER_V4.unpack_from(raw)
    now = datetime.now(timezone.utc).timestamp()
    out.update({
        "ok": True,
        "tracked_positions": count,
        "peak_equity": round(peak_eq, 2),
        "initial_balance": round(initial_balance, 2),
        "leverage": leverage,
        "loss_streak": streak_losses,
        "consecutive_losses": consec_loss,
        "streak_paused": bool(streak_paused),
        "paper": None if paper_dir == 0 else {
            "side": "BUY" if paper_dir > 0 else "SELL",
            "entry": paper_entry, "target": paper_target, "stop": paper_stop,
        },
        # Only report pauses that are still in the future
        "cooldown_until_vn": _vn(cooldown_bar) if cooldown_bar > now else None,
        "equity_pause_until_vn": _vn(eq_pause) if eq_pause > now else None,
        "basket_pause_until_vn": _vn(basket_pause) if basket_pause > now else None,
        "saved_at_vn": _vn(saved_at),
    })
    return out


def read_bot_states(data_path, login):
    """All EA state files for this account. data_path = mt5.terminal_info().data_path."""
    if not data_path:
        return []
    folder = os.path.join(data_path, "MQL5", "Files", "NyaoScalper")
    states = []
    for path in sorted(glob.glob(os.path.join(folder, f"state_{int(login)}_*.bin"))):
        m = FILE_RE.match(os.path.basename(path))
        if not m:
            continue
        try:
            states.append(_decode(path, m.group(2), int(m.group(3))))
        except (OSError, struct.error):
            continue
    return states
