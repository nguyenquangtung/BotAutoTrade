"""
Nyao Scalper - Database PnL & Tick History Analytics Engine
Uses SQLite3 (standard library) with persistent storage and connection pooling/thread safety.
Stores:
1. Account equity snapshots (every 60s) for drawdown and equity curve tracking.
2. Full closed deal history with Win Rate, Profit Factor, and Net PnL metrics.
3. Live tick history and spread monitoring for XAUUSDc and USDJPYc.
"""

import os
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

TZ_VN = ZoneInfo("Asia/Ho_Chi_Minh")
logger = logging.getLogger("AnalyticsDB")

DEFAULT_DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DEFAULT_DB_PATH = os.path.join(DEFAULT_DB_DIR, "trading_analytics.db")


class AnalyticsDatabase:
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
        except Exception:
            conn.execute("PRAGMA journal_mode = DELETE;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        return conn

    def _init_db(self):
        """Initializes database schema with optimized indices."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # 1. Account Snapshots Table
            cursor.execute("""
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
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_epoch ON account_snapshots(timestamp_epoch);")

            # 2. Closed Deals Table
            cursor.execute("""
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
                    deal_time_epoch INTEGER NOT NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_deals_symbol ON closed_deals(symbol);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_deals_epoch ON closed_deals(deal_time_epoch);")

            # 3. Tick History Table
            cursor.execute("""
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
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_ticks_symbol_time ON tick_history(symbol, time_msc);")

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
        now_epoch = int(now_utc.timestamp())

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO account_snapshots (
                    timestamp_utc, timestamp_vn, timestamp_epoch,
                    balance, equity, margin, free_margin, floating_pnl, open_positions_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                now_utc.strftime("%Y-%m-%d %H:%M:%S"),
                now_vn.strftime("%Y-%m-%d %H:%M:%S"),
                now_epoch,
                round(balance, 2),
                round(equity, 2),
                round(margin, 2),
                round(free_margin, 2),
                round(floating_pnl, 2),
                open_positions_count
            ))
            conn.commit()

    def sync_closed_deals(self, raw_deals: List[Any], is_cent: bool = True) -> int:
        """
        Inserts closed deals into closed_deals table (INSERT OR IGNORE to prevent duplicates).
        Returns number of newly inserted deals.
        """
        if not raw_deals:
            return 0

        inserted_count = 0
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for d in raw_deals:
                # MT5 deal entry 1 = DEAL_ENTRY_OUT (trade close)
                # entry 2 = DEAL_ENTRY_INOUT (reversal)
                if getattr(d, "entry", 0) not in (1, 2):
                    continue

                deal_ticket = int(d.ticket)
                order_id = int(d.order)
                position_id = int(getattr(d, "position_id", order_id))
                symbol = str(d.symbol)
                trade_type = "BUY" if d.type == 0 else "SELL"
                volume = round(float(d.volume), 2)
                price = round(float(d.price), 5)
                profit = round(float(d.profit), 2)
                profit_cent = profit if is_cent else profit * 100.0
                profit_usd = profit / 100.0 if is_cent else profit
                commission = round(float(getattr(d, "commission", 0.0)), 2)
                swap = round(float(getattr(d, "swap", 0.0)), 2)
                magic = int(getattr(d, "magic", 0))
                comment = str(getattr(d, "comment", ""))
                deal_time_epoch = int(d.time)

                dt_utc = datetime.fromtimestamp(deal_time_epoch, tz=timezone.utc)
                dt_vn = dt_utc.astimezone(TZ_VN)

                cursor.execute("""
                    INSERT OR IGNORE INTO closed_deals (
                        deal_ticket, order_id, position_id, symbol, trade_type,
                        volume, price, sl, tp, profit_cent, profit_usd,
                        commission, swap, magic, comment,
                        deal_time_utc, deal_time_vn, deal_time_epoch
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    deal_ticket, order_id, position_id, symbol, trade_type,
                    volume, price, 0.0, 0.0, profit_cent, profit_usd,
                    commission, swap, magic, comment,
                    dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
                    dt_vn.strftime("%Y-%m-%d %H:%M:%S"),
                    deal_time_epoch
                ))
                if cursor.rowcount > 0:
                    inserted_count += 1

            conn.commit()
        return inserted_count

    def record_tick(self, symbol: str, bid: float, ask: float, time_msc: int):
        """Records latest tick and calculates spread in points."""
        now_utc = datetime.fromtimestamp(time_msc / 1000.0, tz=timezone.utc)
        now_vn = now_utc.astimezone(TZ_VN)
        
        # Point size heuristic
        point_multiplier = 100.0 if "jpy" in symbol.lower() or "xau" in symbol.lower() else 100000.0
        spread_points = round(abs(ask - bid) * point_multiplier, 1)

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO tick_history (symbol, time_msc, time_vn, bid, ask, spread_points)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                symbol, time_msc, now_vn.strftime("%Y-%m-%d %H:%M:%S"),
                round(bid, 5), round(ask, 5), spread_points
            ))
            conn.commit()

    def prune_old_ticks(self, retention_days: int = 3):
        """Prunes tick history older than retention_days to keep database lightweight."""
        cutoff_epoch_msc = int((datetime.now(timezone.utc) - timedelta(days=retention_days)).timestamp() * 1000)
        with self._get_connection() as conn:
            conn.execute("DELETE FROM tick_history WHERE time_msc < ?", (cutoff_epoch_msc,))
            conn.commit()

    # ==========================
    # ANALYTICS & METRICS
    # ==========================

    def get_performance_summary(self) -> Dict[str, Any]:
        """Calculates comprehensive trading performance metrics."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # All closed deals
            cursor.execute("""
                SELECT deal_ticket, symbol, trade_type, volume, price,
                       profit_cent, profit_usd, deal_time_vn, deal_time_epoch
                FROM closed_deals
                ORDER BY deal_time_epoch ASC
            """)
            deals = cursor.fetchall()

            total_trades = len(deals)
            if total_trades == 0:
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
                    "profit_factor": 0.0,
                    "max_drawdown_usc": 0.0,
                    "max_drawdown_pct": 0.0,
                    "avg_win_usc": 0.0,
                    "avg_loss_usc": 0.0,
                    "symbols": {}
                }

            win_trades = 0
            loss_trades = 0
            be_trades = 0
            gross_profit = 0.0
            gross_loss = 0.0
            net_profit = 0.0

            symbols_stat: Dict[str, Dict[str, Any]] = {}
            running_pnl = 0.0
            peak_pnl = 0.0
            max_dd_usc = 0.0

            for row in deals:
                p = row["profit_cent"]
                sym = row["symbol"]
                net_profit += p
                running_pnl += p
                if running_pnl > peak_pnl:
                    peak_pnl = running_pnl
                dd = peak_pnl - running_pnl
                if dd > max_dd_usc:
                    max_dd_usc = dd

                if sym not in symbols_stat:
                    symbols_stat[sym] = {"trades": 0, "wins": 0, "profit_usc": 0.0}
                symbols_stat[sym]["trades"] += 1
                symbols_stat[sym]["profit_usc"] += p

                if p > 0.05:
                    win_trades += 1
                    gross_profit += p
                    symbols_stat[sym]["wins"] += 1
                elif p < -0.05:
                    loss_trades += 1
                    gross_loss += abs(p)
                else:
                    be_trades += 1

            win_rate = round((win_trades / total_trades) * 100.0, 1) if total_trades > 0 else 0.0
            profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
            avg_win = round(gross_profit / win_trades, 2) if win_trades > 0 else 0.0
            avg_loss = round(gross_loss / loss_trades, 2) if loss_trades > 0 else 0.0

            # Round symbols
            for k in symbols_stat:
                symbols_stat[k]["profit_usc"] = round(symbols_stat[k]["profit_usc"], 2)
                symbols_stat[k]["profit_usd"] = round(symbols_stat[k]["profit_usc"] / 100.0, 4)

            return {
                "total_trades": total_trades,
                "win_trades": win_trades,
                "loss_trades": loss_trades,
                "be_trades": be_trades,
                "win_rate_pct": win_rate,
                "gross_profit_usc": round(gross_profit, 2),
                "gross_loss_usc": round(gross_loss, 2),
                "net_profit_usc": round(net_profit, 2),
                "net_profit_usd": round(net_profit / 100.0, 4),
                "profit_factor": profit_factor,
                "max_drawdown_usc": round(max_dd_usc, 2),
                "max_drawdown_usd": round(max_dd_usc / 100.0, 4),
                "avg_win_usc": avg_win,
                "avg_loss_usc": avg_loss,
                "symbols": symbols_stat
            }

    def get_equity_curve(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Returns the latest equity snapshot points for charting."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT timestamp_vn, balance, equity, floating_pnl
                FROM account_snapshots
                ORDER BY id DESC
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
            return [
                {
                    "time": r["timestamp_vn"],
                    "balance": r["balance"],
                    "equity": r["equity"],
                    "floating": r["floating_pnl"]
                }
                for r in reversed(rows)
            ]

    def get_recent_closed_deals(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns list of closed deals ordered by deal time desc."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT deal_ticket, order_id, symbol, trade_type, volume, price,
                       profit_cent, profit_usd, deal_time_vn
                FROM closed_deals
                ORDER BY deal_time_epoch DESC
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def get_latest_ticks(self) -> Dict[str, Dict[str, Any]]:
        """Returns the most recent tick for each tracked symbol."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t1.symbol, t1.bid, t1.ask, t1.spread_points, t1.time_vn
                FROM tick_history t1
                INNER JOIN (
                    SELECT symbol, MAX(id) as max_id
                    FROM tick_history
                    GROUP BY symbol
                ) t2 ON t1.id = t2.max_id
            """)
            rows = cursor.fetchall()
            return {r["symbol"]: dict(r) for r in rows}

