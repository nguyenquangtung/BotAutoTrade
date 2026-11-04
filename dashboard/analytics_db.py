"""
Nyao Scalper - Database PnL & Tick History Analytics Engine
Uses SQLite3 (standard library) with ONE long-lived connection per AnalyticsDatabase,
shared across threads (check_same_thread=False) and serialized by a threading.Lock.

Stores:
1. account_snapshots   - account equity snapshots (every 60s) for equity drawdown / curve.
2. closed_deals        - closed (exit) deal history incl. commission, swap, fee and net.
3. tick_history        - NOTE: despite the name these are NOT every broker tick; they are
                         the latest quote sampled once per watchdog loop (~5s). The table name
                         is kept for backward compatibility.
4. spread_stats_minute - per-symbol per-minute spread aggregates (samples/min/avg/max, points).

Net P&L definition lives in metrics.py (profit + commission + swap + fee).
"""

import os
import sqlite3
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from metrics import deal_net, classify, DrawdownTracker, max_drawdown

TZ_VN = ZoneInfo("Asia/Ho_Chi_Minh")
logger = logging.getLogger("AnalyticsDB")

DEFAULT_DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DEFAULT_DB_PATH = os.environ.get("ANALYTICS_DB_PATH") or os.path.join(DEFAULT_DB_DIR, "trading_analytics.db")


def _thin(rows: list, max_points: int) -> list:
    """Keep at most max_points rows, evenly spaced, always including the last one."""
    n = len(rows)
    if max_points <= 0 or n <= max_points:
        return list(rows)
    step = n / float(max_points)
    picked = [rows[int(i * step)] for i in range(max_points - 1)]
    picked.append(rows[-1])
    return picked


def _empty_summary() -> Dict[str, Any]:
    return {
        "total_trades": 0,
        "win_trades": 0,
        "loss_trades": 0,
        "be_trades": 0,
        "win_rate_pct": 0.0,
        "gross_profit_usc": 0.0,
        "gross_loss_usc": 0.0,
        "net_profit_usc": 0.0,
        "net_profit_usd": 0.0,
        "raw_profit_usc": 0.0,
        "commission_usc": 0.0,
        "swap_usc": 0.0,
        "fee_usc": 0.0,
        "profit_factor": 0.0,
        "max_drawdown_usc": 0.0,
        "max_drawdown_usd": 0.0,
        "max_drawdown_pct": 0.0,
        "max_drawdown_equity_usc": 0.0,
        "max_drawdown_equity_usd": 0.0,
        "max_drawdown_equity_pct": 0.0,
        "avg_win_usc": 0.0,
        "avg_loss_usc": 0.0,
        "symbols": {},
    }


class AnalyticsDatabase:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        # Cached closed-deal summary; invalidated when sync_closed_deals changes rows.
        self._deal_summary_cache: Optional[Dict[str, Any]] = None
        self._deals_generation = 0
        # Incremental equity drawdown state over account_snapshots.
        self._eq_last_id = 0
        self._eq_tracker = DrawdownTracker()
        self._open()
        self._init_db()

    # ==========================
    # CONNECTION
    # ==========================

    def _open(self):
        conn = sqlite3.connect(self.db_path, timeout=15.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # WAL is not reliable under Wine's filesystem layer; fall back to DELETE journal.
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
        except Exception:
            conn.execute("PRAGMA journal_mode = DELETE;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        self._conn = conn

    def close(self):
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    def _init_db(self):
        """Initializes schema, indices and migrations."""
        with self._lock:
            conn = self._conn
            cur = conn.cursor()

            cur.execute("""
                CREATE TABLE IF NOT EXISTS account_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp_utc TEXT NOT NULL,
                    timestamp_vn TEXT NOT NULL,
                    timestamp_epoch INTEGER NOT NULL,
                    balance REAL NOT NULL,
                    equity REAL NOT NULL,
                    margin REAL NOT NULL,
                    free_margin REAL NOT NULL,
                    floating_pnl REAL NOT NULL,
                    open_positions_count INTEGER NOT NULL
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_epoch ON account_snapshots(timestamp_epoch);")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS closed_deals (
                    deal_ticket INTEGER PRIMARY KEY,
                    order_id INTEGER NOT NULL,
                    position_id INTEGER,
                    symbol TEXT NOT NULL,
                    trade_type TEXT NOT NULL,
                    volume REAL NOT NULL,
                    price REAL NOT NULL,
                    sl REAL DEFAULT 0,
                    tp REAL DEFAULT 0,
                    profit_cent REAL NOT NULL,
                    profit_usd REAL NOT NULL,
                    commission REAL DEFAULT 0,
                    swap REAL DEFAULT 0,
                    magic INTEGER DEFAULT 0,
                    comment TEXT DEFAULT '',
                    deal_time_utc TEXT NOT NULL,
                    deal_time_vn TEXT NOT NULL,
                    deal_time_epoch INTEGER NOT NULL,
                    fee REAL DEFAULT 0,
                    net_cent REAL
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_deals_symbol ON closed_deals(symbol);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_deals_epoch ON closed_deals(deal_time_epoch);")

            # tick_history = ~5s quote samples (see module docstring), name kept for compatibility.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tick_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    time_msc INTEGER NOT NULL,
                    time_vn TEXT NOT NULL,
                    bid REAL NOT NULL,
                    ask REAL NOT NULL,
                    spread_points REAL NOT NULL
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_ticks_symbol_time ON tick_history(symbol, time_msc);")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS spread_stats_minute (
                    symbol TEXT NOT NULL,
                    minute_epoch INTEGER NOT NULL,
                    samples INTEGER NOT NULL,
                    spread_min REAL NOT NULL,
                    spread_avg REAL NOT NULL,
                    spread_max REAL NOT NULL,
                    PRIMARY KEY (symbol, minute_epoch)
                );
            """)

            # ---- Migrations for DBs created by older versions ----
            cols = {r["name"] for r in cur.execute("PRAGMA table_info(closed_deals);").fetchall()}
            if "fee" not in cols:
                cur.execute("ALTER TABLE closed_deals ADD COLUMN fee REAL DEFAULT 0;")
                logger.info("Migration: added closed_deals.fee")
            if "net_cent" not in cols:
                cur.execute("ALTER TABLE closed_deals ADD COLUMN net_cent REAL;")
                logger.info("Migration: added closed_deals.net_cent")
            # Back-fill net for legacy rows. Legacy rows were written on the cent account, where
            # commission/swap are already in USC. Fee was never stored before -> 0 until the
            # next full 30-day sync refreshes it from MT5 (sync_closed_deals updates changed rows).
            cur.execute("""
                UPDATE closed_deals
                SET net_cent = ROUND(profit_cent + COALESCE(commission, 0) + COALESCE(swap, 0) + COALESCE(fee, 0), 2)
                WHERE net_cent IS NULL;
            """)
            conn.commit()
            logger.info(f"Analytics SQLite Database initialized at {self.db_path}")

    # ==========================
    # DATA INGESTION
    # ==========================

    def record_account_snapshot(self, balance: float, equity: float, margin: float,
                                free_margin: float, floating_pnl: float, open_positions_count: int):
        """Records an account state snapshot."""
        now_utc = datetime.now(timezone.utc)
        now_vn = now_utc.astimezone(TZ_VN)
        with self._lock:
            self._conn.execute("""
                INSERT INTO account_snapshots (
                    timestamp_utc, timestamp_vn, timestamp_epoch,
                    balance, equity, margin, free_margin, floating_pnl, open_positions_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                now_utc.strftime("%Y-%m-%d %H:%M:%S"),
                now_vn.strftime("%Y-%m-%d %H:%M:%S"),
                int(now_utc.timestamp()),
                round(balance, 2), round(equity, 2), round(margin, 2),
                round(free_margin, 2), round(floating_pnl, 2), open_positions_count
            ))
            self._conn.commit()

    def sync_closed_deals(self, raw_deals: List[Any], is_cent: bool = True) -> int:
        """
        Inserts exit deals into closed_deals; existing rows whose commission/swap/fee changed
        (e.g. legacy rows without fee) are refreshed. Returns number of inserted + updated rows.
        """
        if not raw_deals:
            return 0

        changed = 0
        scale = 1.0 if is_cent else 100.0  # account currency -> cents
        with self._lock:
            cur = self._conn.cursor()
            for d in raw_deals:
                # MT5 deal entry 1 = DEAL_ENTRY_OUT (close), 2 = DEAL_ENTRY_INOUT (reversal)
                if getattr(d, "entry", 0) not in (1, 2):
                    continue

                deal_ticket = int(d.ticket)
                order_id = int(d.order)
                position_id = int(getattr(d, "position_id", order_id))
                profit = round(float(d.profit), 2)
                commission = round(float(getattr(d, "commission", 0.0) or 0.0), 2)
                swap = round(float(getattr(d, "swap", 0.0) or 0.0), 2)
                fee = round(float(getattr(d, "fee", 0.0) or 0.0), 2)
                profit_cent = profit * scale
                profit_usd = profit_cent / 100.0
                net_cent = round(deal_net(profit, commission, swap, fee) * scale, 2)
                deal_time_epoch = int(d.time)
                dt_utc = datetime.fromtimestamp(deal_time_epoch, tz=timezone.utc)
                dt_vn = dt_utc.astimezone(TZ_VN)

                cur.execute("""
                    INSERT OR IGNORE INTO closed_deals (
                        deal_ticket, order_id, position_id, symbol, trade_type,
                        volume, price, sl, tp, profit_cent, profit_usd,
                        commission, swap, magic, comment,
                        deal_time_utc, deal_time_vn, deal_time_epoch, fee, net_cent
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    deal_ticket, order_id, position_id, str(d.symbol),
                    "BUY" if d.type == 0 else "SELL",
                    round(float(d.volume), 2), round(float(d.price), 5), 0.0, 0.0,
                    profit_cent, profit_usd, commission, swap,
                    int(getattr(d, "magic", 0)), str(getattr(d, "comment", "")),
                    dt_utc.strftime("%Y-%m-%d %H:%M:%S"), dt_vn.strftime("%Y-%m-%d %H:%M:%S"),
                    deal_time_epoch, fee, net_cent
                ))
                if cur.rowcount > 0:
                    changed += 1
                    continue
                cur.execute("""
                    UPDATE closed_deals SET commission = ?, swap = ?, fee = ?, net_cent = ?
                    WHERE deal_ticket = ?
                      AND (commission IS NOT ? OR swap IS NOT ? OR fee IS NOT ? OR net_cent IS NOT ?)
                """, (commission, swap, fee, net_cent, deal_ticket, commission, swap, fee, net_cent))
                if cur.rowcount > 0:
                    changed += 1

            self._conn.commit()
            if changed:
                self._deals_generation += 1
                self._deal_summary_cache = None
        return changed

    def get_max_deal_epoch(self) -> Optional[int]:
        with self._lock:
            row = self._conn.execute("SELECT MAX(deal_time_epoch) AS m FROM closed_deals").fetchone()
        return int(row["m"]) if row and row["m"] is not None else None

    def record_tick(self, symbol: str, bid: float, ask: float, time_msc: int,
                    spread_points: Optional[float]):
        """
        Records one ~5s quote sample and folds it into spread_stats_minute.
        spread_points must be computed by the caller as (ask - bid) / symbol_info.point.
        """
        if spread_points is None:
            return
        spread_points = round(float(spread_points), 1)
        ts_utc = datetime.fromtimestamp(time_msc / 1000.0, tz=timezone.utc)
        ts_vn = ts_utc.astimezone(TZ_VN)
        minute_epoch = (int(time_msc) // 60000) * 60

        with self._lock:
            self._conn.execute("""
                INSERT INTO tick_history (symbol, time_msc, time_vn, bid, ask, spread_points)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (symbol, int(time_msc), ts_vn.strftime("%Y-%m-%d %H:%M:%S"),
                  round(bid, 5), round(ask, 5), spread_points))
            # In an UPSERT's SET clause, bare column names refer to the pre-update row.
            self._conn.execute("""
                INSERT INTO spread_stats_minute (symbol, minute_epoch, samples, spread_min, spread_avg, spread_max)
                VALUES (?, ?, 1, ?, ?, ?)
                ON CONFLICT(symbol, minute_epoch) DO UPDATE SET
                    spread_avg = (spread_avg * samples + excluded.spread_avg) / (samples + 1),
                    samples    = samples + 1,
                    spread_min = MIN(spread_min, excluded.spread_min),
                    spread_max = MAX(spread_max, excluded.spread_max)
            """, (symbol, minute_epoch, spread_points, spread_points, spread_points))
            self._conn.commit()

    def prune_old_ticks(self, retention_days: int = 3, spread_stats_retention_days: int = 30):
        """Prunes tick_history and spread_stats_minute older than their retention windows."""
        now = datetime.now(timezone.utc)
        cutoff_msc = int((now - timedelta(days=retention_days)).timestamp() * 1000)
        cutoff_min = int((now - timedelta(days=spread_stats_retention_days)).timestamp())
        with self._lock:
            self._conn.execute("DELETE FROM tick_history WHERE time_msc < ?", (cutoff_msc,))
            self._conn.execute("DELETE FROM spread_stats_minute WHERE minute_epoch < ?", (cutoff_min,))
            self._conn.commit()

    # ==========================
    # ANALYTICS & METRICS
    # ==========================

    def _compute_deal_summary(self) -> Dict[str, Any]:
        with self._lock:
            deals = self._conn.execute("""
                SELECT symbol, profit_cent, commission, swap, fee, net_cent
                FROM closed_deals
                ORDER BY deal_time_epoch ASC, deal_ticket ASC
            """).fetchall()

        s = _empty_summary()
        total = len(deals)
        if total == 0:
            return s

        wins = losses = bes = 0
        gross_profit = gross_loss = net_profit = 0.0
        raw = comm = swp = fee = 0.0
        symbols_stat: Dict[str, Dict[str, Any]] = {}
        running = []
        cum = 0.0

        for row in deals:
            n = row["net_cent"]
            if n is None:
                n = deal_net(row["profit_cent"], row["commission"], row["swap"], row["fee"])
            sym = row["symbol"]
            net_profit += n
            cum += n
            running.append(cum)
            raw += row["profit_cent"] or 0.0
            comm += row["commission"] or 0.0
            swp += row["swap"] or 0.0
            fee += row["fee"] or 0.0

            st = symbols_stat.setdefault(sym, {"trades": 0, "wins": 0, "losses": 0, "profit_usc": 0.0})
            st["trades"] += 1
            st["profit_usc"] += n

            c = classify(n)
            if c == "win":
                wins += 1
                gross_profit += n
                st["wins"] += 1
            elif c == "loss":
                losses += 1
                gross_loss += abs(n)
                st["losses"] += 1
            else:
                bes += 1

        max_dd, _ = max_drawdown(running, start_peak=0.0)

        for st in symbols_stat.values():
            st["profit_usc"] = round(st["profit_usc"], 2)
            st["profit_usd"] = round(st["profit_usc"] / 100.0, 4)

        s.update({
            "total_trades": total,
            "win_trades": wins,
            "loss_trades": losses,
            "be_trades": bes,
            "win_rate_pct": round(wins / total * 100.0, 1),
            "gross_profit_usc": round(gross_profit, 2),
            "gross_loss_usc": round(gross_loss, 2),
            "net_profit_usc": round(net_profit, 2),
            "net_profit_usd": round(net_profit / 100.0, 4),
            "raw_profit_usc": round(raw, 2),
            "commission_usc": round(comm, 2),
            "swap_usc": round(swp, 2),
            "fee_usc": round(fee, 2),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0),
            "max_drawdown_usc": round(max_dd, 2),
            "max_drawdown_usd": round(max_dd / 100.0, 4),
            "avg_win_usc": round(gross_profit / wins, 2) if wins else 0.0,
            "avg_loss_usc": round(gross_loss / losses, 2) if losses else 0.0,
            "symbols": symbols_stat,
        })
        return s

    def get_equity_drawdown(self) -> Dict[str, float]:
        """
        Peak-to-trough equity drawdown over all account_snapshots (processed incrementally).
        Note: deposits/withdrawals are not filtered out.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, equity FROM account_snapshots WHERE id > ? ORDER BY id ASC",
                (self._eq_last_id,)
            ).fetchall()
            for r in rows:
                self._eq_tracker.add(float(r["equity"]))
                self._eq_last_id = r["id"]
            dd, pct = self._eq_tracker.max_dd, self._eq_tracker.max_dd_pct
        return {
            "max_drawdown_equity_usc": round(dd, 2),
            "max_drawdown_equity_usd": round(dd / 100.0, 4),
            "max_drawdown_equity_pct": round(pct, 2),
        }

    def get_performance_summary(self) -> Dict[str, Any]:
        """Closed-deal metrics (cached until deals change) + equity drawdown."""
        cached = self._deal_summary_cache
        if cached is None:
            gen = self._deals_generation
            cached = self._compute_deal_summary()
            with self._lock:
                if gen == self._deals_generation:  # don't cache a result made stale by a concurrent sync
                    self._deal_summary_cache = cached
        s = dict(cached)
        s["symbols"] = {k: dict(v) for k, v in cached["symbols"].items()}
        s.update(self.get_equity_drawdown())
        return s

    def get_equity_curve(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Returns the latest equity snapshot points for charting."""
        with self._lock:
            rows = self._conn.execute("""
                SELECT timestamp_vn, balance, equity, floating_pnl
                FROM account_snapshots ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
        return [
            {"time": r["timestamp_vn"], "balance": r["balance"], "equity": r["equity"], "floating": r["floating_pnl"]}
            for r in reversed(rows)
        ]

    def get_equity_curve_since(self, since_epoch: int, max_points: int = 720) -> List[Dict[str, Any]]:
        """Equity snapshots since since_epoch, evenly thinned to at most max_points (last point kept)."""
        with self._lock:
            rows = self._conn.execute("""
                SELECT timestamp_vn, balance, equity, floating_pnl
                FROM account_snapshots WHERE timestamp_epoch >= ? ORDER BY id ASC
            """, (int(since_epoch),)).fetchall()
        rows = _thin(rows, max_points)
        return [
            {"time": r["timestamp_vn"], "balance": r["balance"], "equity": r["equity"], "floating": r["floating_pnl"]}
            for r in rows
        ]

    def get_recent_closed_deals(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns closed deals ordered by deal time desc."""
        with self._lock:
            rows = self._conn.execute("""
                SELECT deal_ticket, order_id, symbol, trade_type, volume, price,
                       profit_cent, profit_usd, commission, swap, fee, net_cent, deal_time_vn
                FROM closed_deals ORDER BY deal_time_epoch DESC, deal_ticket DESC LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_closed_deals_since(self, since_epoch: int) -> List[Dict[str, Any]]:
        """Closed deals with deal_time_epoch >= since_epoch, oldest first."""
        with self._lock:
            rows = self._conn.execute("""
                SELECT deal_ticket, order_id, symbol, trade_type, volume, price,
                       profit_cent, profit_usd, commission, swap, fee, net_cent,
                       deal_time_vn, deal_time_utc, deal_time_epoch
                FROM closed_deals WHERE deal_time_epoch >= ?
                ORDER BY deal_time_epoch ASC, deal_ticket ASC
            """, (int(since_epoch),)).fetchall()
        return [dict(r) for r in rows]

    def get_latest_ticks(self) -> Dict[str, Dict[str, Any]]:
        """Returns the most recent sample for each tracked symbol."""
        with self._lock:
            rows = self._conn.execute("""
                SELECT t1.symbol, t1.bid, t1.ask, t1.spread_points, t1.time_vn
                FROM tick_history t1
                INNER JOIN (SELECT symbol, MAX(id) AS max_id FROM tick_history GROUP BY symbol) t2
                ON t1.id = t2.max_id
            """).fetchall()
        return {r["symbol"]: dict(r) for r in rows}

    def get_spread_stats(self, symbol: str, since_epoch: int) -> List[Dict[str, Any]]:
        """Per-minute spread aggregates for a symbol since since_epoch."""
        with self._lock:
            rows = self._conn.execute("""
                SELECT symbol, minute_epoch, samples, spread_min, spread_avg, spread_max
                FROM spread_stats_minute WHERE symbol = ? AND minute_epoch >= ?
                ORDER BY minute_epoch ASC
            """, (symbol, int(since_epoch))).fetchall()
        return [dict(r) for r in rows]

    def get_spread_series(self, symbol: str, since_epoch: int, max_points: int = 360) -> List[Dict[str, Any]]:
        """Per-minute spread aggregates merged into at most max_points buckets (avg of avgs, max of maxes)."""
        rows = self.get_spread_stats(symbol, since_epoch)
        if not rows:
            return []
        n = len(rows)
        size = max(1, -(-n // max_points))  # ceil(n / max_points) minutes per bucket
        out = []
        for i in range(0, n, size):
            chunk = rows[i:i + size]
            samples = sum(r["samples"] for r in chunk) or 1
            out.append({
                "t": datetime.fromtimestamp(chunk[0]["minute_epoch"], tz=timezone.utc).astimezone(TZ_VN).strftime("%Y-%m-%d %H:%M"),
                "avg": round(sum(r["spread_avg"] * r["samples"] for r in chunk) / samples, 1),
                "max": max(r["spread_max"] for r in chunk),
                "min": min(r["spread_min"] for r in chunk),
            })
        return out
