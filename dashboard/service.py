"""
Nyao Scalper - Live Mobile Web Dashboard, Watchdog & Analytics Service
Runs inside MT5 Wine/Python environment. Binds to 127.0.0.1:8001 by default
(env DASHBOARD_HOST / DASHBOARD_PORT); Nginx proxies /dashboard/ to it with Basic Auth.
Zero external dependencies: uses Python standard library http.server + threading + sqlite3.
"""

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler

from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)
from analytics_db import AnalyticsDatabase, TZ_VN  # noqa: E402
from metrics import is_cent_currency  # noqa: E402

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

LOG_FILE = os.environ.get("DASHBOARD_LOG_FILE") or os.path.join(BASE_DIR, "watchdog.log")
HTML_PATH = os.path.join(BASE_DIR, "index.html")
DASHBOARD_HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8001"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"),
    ]
)
logger = logging.getLogger("WatchdogService")

analytics = AnalyticsDatabase()

STATE = {
    "connected": False,
    "last_check": None,
    "account": {},
    "positions": [],
    "today_deals": [],
    "today_profit": 0.0,
    "consecutive_failures": 0,
    "watchdog_status": "Starting",
    "analytics_summary": {},
    "latest_ticks": {},
    "recent_deals": [],
}
STATE_LOCK = threading.Lock()

TRACKED_SYMBOLS = ["XAUUSDc", "USDJPYc"]
INITIAL_HISTORY_DAYS = 30
HISTORY_OVERLAP_SEC = 3600
LOOP_INTERVAL_SEC = 5
SNAPSHOT_INTERVAL_SEC = 60
PRUNE_INTERVAL_SEC = 21600


def _load_html() -> bytes:
    try:
        with open(HTML_PATH, "r", encoding="utf-8") as f:
            return f.read().encode("utf-8")
    except OSError as e:
        logger.error(f"Dashboard HTML not found at {HTML_PATH}: {e}")
        return ("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Dashboard</title></head>"
                "<body style='font-family:sans-serif;padding:16px'>Dashboard UI (index.html) is missing "
                "next to service.py. JSON API: <a href='api/data'>api/data</a> &bull; "
                "<a href='health'>health</a></body></html>").encode("utf-8")


HTML_BYTES = _load_html()


class MT5Session:
    """Holds the MT5 connection: initialize once, re-initialize only after a failure."""

    def __init__(self):
        self.ready = False
        self.point_cache = {}

    def invalidate(self):
        if self.ready and mt5 is not None:
            try:
                mt5.shutdown()
            except Exception:
                pass
        self.ready = False
        self.point_cache.clear()

    def ensure(self):
        """Returns (term_info, error_str)."""
        if mt5 is None:
            return None, "MetaTrader5 library not available"
        if not self.ready:
            if not mt5.initialize():
                return None, f"mt5.initialize() failed: {mt5.last_error()}"
            self.ready = True
            logger.info("MT5 initialized.")
        term = mt5.terminal_info()
        if term is None:
            err = mt5.last_error()
            self.invalidate()
            return None, f"terminal_info() failed, will re-initialize: {err}"
        return term, None

    def point(self, sym):
        p = self.point_cache.get(sym)
        if p:
            return p
        info = mt5.symbol_info(sym)
        if info is None:
            mt5.symbol_select(sym, True)
            info = mt5.symbol_info(sym)
        if info is not None and getattr(info, "point", 0) > 0:
            self.point_cache[sym] = info.point
            return info.point
        return None


SESSION = MT5Session()


def _naive_utc(epoch: float) -> datetime:
    """MT5 history functions take naive datetimes interpreted as UTC (Exness server time = GMT+0)."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(tzinfo=None)


def _vn_day_start_epoch() -> int:
    now_vn = datetime.now(TZ_VN)
    start = datetime(now_vn.year, now_vn.month, now_vn.day, tzinfo=TZ_VN)
    return int(start.timestamp())


class DealSync:
    """Incremental deal-history sync: first 30 days, then from last deal time minus overlap."""

    def __init__(self):
        self.full_sync_done = False

    def run(self, is_cent: bool) -> int:
        now = time.time()
        if not self.full_sync_done:
            start_epoch = now - INITIAL_HISTORY_DAYS * 86400
        else:
            last = analytics.get_max_deal_epoch()
            start_epoch = (last - HISTORY_OVERLAP_SEC) if last else now - INITIAL_HISTORY_DAYS * 86400
        # Upper bound slightly in the future so deals stamped "now" are never cut off.
        raw = mt5.history_deals_get(_naive_utc(start_epoch), _naive_utc(now + 86400))
        if raw is None:
            # None = error (empty history returns an empty tuple); retry full window next time.
            logger.warning(f"history_deals_get failed: {mt5.last_error()}")
            return 0
        self.full_sync_done = True
        return analytics.sync_closed_deals(raw, is_cent=is_cent) if raw else 0


DEAL_SYNC = DealSync()


def _format_today_deals(rows):
    out, total = [], 0.0
    for r in rows:
        dt_utc = datetime.fromtimestamp(r["deal_time_epoch"], tz=timezone.utc)
        dt_vn = dt_utc.astimezone(TZ_VN)
        net = r["net_cent"] if r.get("net_cent") is not None else r["profit_cent"]
        total += net
        out.append({
            "ticket": r["deal_ticket"],
            "order": r["order_id"],
            "time": dt_vn.strftime("%H:%M:%S"),
            "time_vn": dt_vn.strftime("%H:%M:%S"),
            "time_server": dt_utc.strftime("%H:%M:%S"),
            "symbol": r["symbol"],
            "type": r["trade_type"],
            "volume": round(r["volume"], 2),
            "price": round(r["price"], 3),
            "profit_cent": round(r["profit_cent"], 2),
            "profit_usd": round(r["profit_cent"] / 100.0, 4),
            "net_cent": round(net, 2),
            "net_usd": round(net / 100.0, 4),
        })
    return out, total


def get_mt5_snapshot():
    """Queries MT5 for account, positions and ticks; syncs deals incrementally.
    Returns (ok, data_or_error, deals_changed)."""
    term_info, err = SESSION.ensure()
    if err:
        return False, err, 0
    if not term_info.connected:
        return False, "MT5 terminal disconnected from broker", 0

    acc = mt5.account_info()
    if acc is None:
        return False, "Failed to retrieve account_info", 0

    is_cent = is_cent_currency(acc.currency)
    to_usd = (lambda v: v / 100.0) if is_cent else (lambda v: v)

    positions = []
    for p in (mt5.positions_get() or []):
        dt_server = datetime.fromtimestamp(p.time, tz=timezone.utc)
        dt_vn = dt_server.astimezone(TZ_VN)
        positions.append({
            "ticket": p.ticket,
            "time": dt_vn.strftime("%H:%M:%S"),
            "time_vn": dt_vn.strftime("%H:%M:%S"),
            "time_server": dt_server.strftime("%H:%M:%S"),
            "symbol": p.symbol,
            "type": "BUY" if p.type == 0 else "SELL",
            "volume": round(p.volume, 2),
            "price_open": round(p.price_open, 3),
            "price_current": round(p.price_current, 3),
            "sl": round(p.sl, 3),
            "tp": round(p.tp, 3),
            "profit_cent": round(p.profit, 2),
            "profit_usd": round(to_usd(p.profit), 4),
        })

    deals_changed = DEAL_SYNC.run(is_cent)

    latest_ticks = {}
    for sym in TRACKED_SYMBOLS:
        tick = mt5.symbol_info_tick(sym)
        if tick and tick.time_msc > 0:
            point = SESSION.point(sym)
            spread_pts = round((tick.ask - tick.bid) / point, 1) if point else None
            analytics.record_tick(sym, tick.bid, tick.ask, tick.time_msc, spread_pts)
            latest_ticks[sym] = {
                "bid": tick.bid,
                "ask": tick.ask,
                "spread_points": spread_pts,
                "time": datetime.fromtimestamp(tick.time_msc / 1000.0, tz=timezone.utc).astimezone(TZ_VN).strftime("%H:%M:%S"),
            }

    balance, equity = acc.balance, acc.equity
    floating = equity - balance
    data = {
        "connected": True,
        "server": acc.server,
        "login": acc.login,
        "currency": acc.currency,
        "is_cent": is_cent,
        "balance": round(balance, 2),
        "balance_usd": round(to_usd(balance), 2),
        "equity": round(equity, 2),
        "equity_usd": round(to_usd(equity), 2),
        "margin": round(acc.margin, 2),
        "free_margin": round(acc.margin_free, 2),
        "margin_level": round(acc.margin_level, 2) if acc.margin > 0 else 0.0,
        "floating": round(floating, 2),
        "floating_usd": round(to_usd(floating), 2),
        "positions": positions,
        "ping": term_info.ping_last / 1000.0 if hasattr(term_info, "ping_last") else 0,
        "latest_ticks": latest_ticks,
    }
    return True, data, deals_changed


def watchdog_worker():
    """Background thread: polls MT5, writes DB, then swaps STATE under the lock (no I/O under lock)."""
    logger.info("Watchdog & Analytics background worker started.")
    last_snapshot_time = 0.0
    last_prune_time = 0.0
    today_cache_key = None          # (vn_day_start_epoch, deals_version)
    deals_version = 0
    today_deals, today_profit = [], 0.0
    summary = None
    recent_deals = None

    while True:
        try:
            ok, res, deals_changed = get_mt5_snapshot()
            now_time = time.time()
            update = {"last_check": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

            if ok:
                summary_dirty = summary is None or deals_changed > 0
                if deals_changed:
                    deals_version += 1
                    recent_deals = None

                if now_time - last_snapshot_time >= SNAPSHOT_INTERVAL_SEC:
                    analytics.record_account_snapshot(
                        balance=res["balance"], equity=res["equity"], margin=res["margin"],
                        free_margin=res["free_margin"], floating_pnl=res["floating"],
                        open_positions_count=len(res["positions"]),
                    )
                    last_snapshot_time = now_time
                    summary_dirty = True  # equity drawdown may have moved

                if summary_dirty:
                    summary = analytics.get_performance_summary()
                if recent_deals is None:
                    recent_deals = analytics.get_recent_closed_deals(limit=50)

                day_start = _vn_day_start_epoch()
                if today_cache_key != (day_start, deals_version):
                    today_deals, today_profit = _format_today_deals(analytics.get_closed_deals_since(day_start))
                    today_cache_key = (day_start, deals_version)

                res["today_deals"] = today_deals
                res["today_profit"] = round(today_profit, 2)
                res["today_profit_usd"] = round(today_profit / 100.0, 2) if res["is_cent"] else round(today_profit, 2)

                if now_time - last_prune_time >= PRUNE_INTERVAL_SEC:
                    analytics.prune_old_ticks(retention_days=3, spread_stats_retention_days=30)
                    last_prune_time = now_time

                update.update({
                    "connected": True,
                    "account": res,
                    "positions": res["positions"],
                    "today_deals": today_deals,
                    "today_profit": res["today_profit"],
                    "latest_ticks": res.get("latest_ticks", {}),
                    "analytics_summary": summary,
                    "recent_deals": recent_deals,
                    "consecutive_failures": 0,
                    "watchdog_status": "Healthy (Connected)",
                })
                with STATE_LOCK:
                    STATE.update(update)
            else:
                with STATE_LOCK:
                    STATE.update(update)
                    STATE["connected"] = False
                    STATE["consecutive_failures"] += 1
                    STATE["watchdog_status"] = f"Warning: {res} (Failed {STATE['consecutive_failures']}x)"
                logger.warning(f"Watchdog check failed: {res}")
        except Exception as e:
            logger.exception(f"Watchdog loop exception: {e}")
            SESSION.invalidate()  # force a clean re-initialize next loop
            with STATE_LOCK:
                STATE["connected"] = False
                STATE["consecutive_failures"] += 1
                STATE["watchdog_status"] = f"Error: {e}"

        time.sleep(LOOP_INTERVAL_SEC)


class DashboardHTTPHandler(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code, obj):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        clean_path = self.path.split("?")[0].rstrip("/")
        if clean_path in ("", "/index.html", "/dashboard"):
            self._send(200, HTML_BYTES, "text/html; charset=utf-8")
        elif clean_path.endswith("/api/data"):
            with STATE_LOCK:
                resp = {
                    "connected": STATE["connected"],
                    "watchdog_status": STATE["watchdog_status"],
                    "last_check": STATE["last_check"],
                    "account": STATE["account"],
                    "positions": STATE["positions"],
                    "today_deals": STATE["today_deals"],
                    "analytics_summary": STATE["analytics_summary"],
                    "latest_ticks": STATE["latest_ticks"],
                    "recent_deals": STATE["recent_deals"],
                }
            # STATE values are replaced wholesale by the worker (never mutated), so serializing
            # outside the lock is safe.
            self._send_json(200, resp)
        elif clean_path.endswith("/api/analytics"):
            # DB calls happen outside STATE_LOCK; AnalyticsDatabase has its own lock.
            self._send_json(200, {
                "summary": analytics.get_performance_summary(),
                "equity_curve": analytics.get_equity_curve(limit=100),
                "recent_deals": analytics.get_recent_closed_deals(limit=50),
            })
        elif clean_path.endswith("/health"):
            with STATE_LOCK:
                connected = STATE["connected"]
                st = {
                    "status": "UP" if connected else "DOWN",
                    "watchdog": STATE["watchdog_status"],
                    "failures": STATE["consecutive_failures"],
                    "last_check": STATE["last_check"],
                }
            self._send_json(200 if connected else 503, st)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, format, *args):
        return  # silence access logs


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def run_server(host=DASHBOARD_HOST, port=DASHBOARD_PORT):
    server = ThreadedHTTPServer((host, port), DashboardHTTPHandler)
    logger.info(f"Dashboard Web Server listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping server...")
    finally:
        server.server_close()
        analytics.close()


if __name__ == "__main__":
    t = threading.Thread(target=watchdog_worker, daemon=True)
    t.start()
    run_server()
