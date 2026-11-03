"""
Nyao Scalper - Live Mobile Web Dashboard, Watchdog & Analytics Service
Runs inside MT5 Wine/Python environment, serving on port 8001.
Zero external dependencies: uses Python standard library http.server + threading + sqlite3.
"""

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

# Import our local SQLite Analytics DB
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from analytics_db import AnalyticsDatabase, TZ_VN

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchdog.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8")
    ]
)
logger = logging.getLogger("WatchdogService")

# Analytics DB instance
analytics = AnalyticsDatabase()

# Global health & state tracking
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
    "latest_ticks": {}
}

STATE_LOCK = threading.Lock()
TRACKED_SYMBOLS = ["XAUUSDc", "USDJPYc"]


def get_mt5_snapshot():
    """Queries MT5 API for real-time account, positions, and closed deals."""
    if mt5 is None:
        return False, "MetaTrader5 library not available"

    if not mt5.initialize():
        return False, f"mt5.initialize() failed: {mt5.last_error()}"

    term_info = mt5.terminal_info()
    if term_info is None or not term_info.connected:
        return False, "MT5 terminal disconnected from broker"

    acc = mt5.account_info()
    if acc is None:
        return False, "Failed to retrieve account_info"

    # Positions
    raw_positions = mt5.positions_get()
    positions = []
    if raw_positions:
        for p in raw_positions:
            dt_vn = datetime.fromtimestamp(p.time, tz=timezone.utc).astimezone(TZ_VN)
            dt_server = datetime.fromtimestamp(p.time, tz=timezone.utc)
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
                "profit_usd": round(p.profit / 100.0, 4) if "c" in p.symbol.lower() else round(p.profit, 2)
            })

    # Deals: Fetch past 30 days to sync full history into SQLite
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    start_history_utc = now_utc - timedelta(days=30)
    raw_deals = mt5.history_deals_get(start_history_utc, now_utc)

    is_cent = "c" in acc.currency.lower() or "usc" in acc.currency.lower()

    # Sync deals into SQLite
    if raw_deals:
        analytics.sync_closed_deals(raw_deals, is_cent=is_cent)

    # Today's deals (since 00:00:00 VN time today)
    now_vn = datetime.now(TZ_VN)
    start_of_day_vn = datetime(now_vn.year, now_vn.month, now_vn.day, 0, 0, 0, tzinfo=TZ_VN)
    start_of_day_utc = start_of_day_vn.astimezone(timezone.utc).replace(tzinfo=None)

    today_deals = []
    today_profit = 0.0
    if raw_deals:
        for d in raw_deals:
            if d.entry in (1, 2):
                d_time_utc = datetime.fromtimestamp(d.time, tz=timezone.utc).replace(tzinfo=None)
                if d_time_utc >= start_of_day_utc:
                    today_profit += d.profit
                    d_vn = datetime.fromtimestamp(d.time, tz=timezone.utc).astimezone(TZ_VN)
                    d_server = datetime.fromtimestamp(d.time, tz=timezone.utc)
                    today_deals.append({
                        "ticket": d.ticket,
                        "order": d.order,
                        "time": d_vn.strftime("%H:%M:%S"),
                        "time_vn": d_vn.strftime("%H:%M:%S"),
                        "time_server": d_server.strftime("%H:%M:%S"),
                        "symbol": d.symbol,
                        "type": "BUY" if d.type == 0 else "SELL",
                        "volume": round(d.volume, 2),
                        "price": round(d.price, 3),
                        "profit_cent": round(d.profit, 2),
                        "profit_usd": round(d.profit / 100.0, 4) if is_cent else round(d.profit, 2)
                    })

    # Capture ticks for tracked symbols
    latest_ticks = {}
    for sym in TRACKED_SYMBOLS:
        tick = mt5.symbol_info_tick(sym)
        if tick and tick.time_msc > 0:
            analytics.record_tick(sym, tick.bid, tick.ask, tick.time_msc)
            latest_ticks[sym] = {
                "bid": tick.bid,
                "ask": tick.ask,
                "spread_points": round(abs(tick.ask - tick.bid) * (100.0 if "jpy" in sym.lower() or "xau" in sym.lower() else 100000.0), 1),
                "time": datetime.fromtimestamp(tick.time_msc / 1000.0, tz=timezone.utc).astimezone(TZ_VN).strftime("%H:%M:%S")
            }

    balance = acc.balance
    equity = acc.equity
    floating = equity - balance

    data = {
        "connected": True,
        "server": acc.server,
        "login": acc.login,
        "currency": acc.currency,
        "is_cent": is_cent,
        "balance": round(balance, 2),
        "balance_usd": round(balance / 100.0, 2) if is_cent else round(balance, 2),
        "equity": round(equity, 2),
        "equity_usd": round(equity / 100.0, 2) if is_cent else round(equity, 2),
        "margin": round(acc.margin, 2),
        "free_margin": round(acc.margin_free, 2),
        "margin_level": round(acc.margin_level, 2) if acc.margin > 0 else 0.0,
        "floating": round(floating, 2),
        "floating_usd": round(floating / 100.0, 2) if is_cent else round(floating, 2),
        "positions": positions,
        "today_deals": today_deals,
        "today_profit": round(today_profit, 2),
        "today_profit_usd": round(today_profit / 100.0, 2) if is_cent else round(today_profit, 2),
        "ping": term_info.ping_last / 1000.0 if hasattr(term_info, "ping_last") else 0,
        "latest_ticks": latest_ticks
    }
    return True, data


def watchdog_worker():
    """Background thread polling MT5 status and logging snapshots."""
    logger.info("Watchdog & Analytics background worker started.")
    last_snapshot_time = 0
    last_prune_time = 0

    while True:
        try:
            ok, res = get_mt5_snapshot()
            now_time = time.time()

            with STATE_LOCK:
                STATE["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if ok:
                    STATE["connected"] = True
                    STATE["account"] = res
                    STATE["positions"] = res["positions"]
                    STATE["today_deals"] = res["today_deals"]
                    STATE["today_profit"] = res["today_profit"]
                    STATE["latest_ticks"] = res.get("latest_ticks", {})
                    STATE["consecutive_failures"] = 0
                    STATE["watchdog_status"] = "Healthy (Connected)"

                    # Record equity snapshot every 60s
                    if now_time - last_snapshot_time >= 60:
                        analytics.record_account_snapshot(
                            balance=res["balance"],
                            equity=res["equity"],
                            margin=res["margin"],
                            free_margin=res["free_margin"],
                            floating_pnl=res["floating"],
                            open_positions_count=len(res["positions"])
                        )
                        last_snapshot_time = now_time

                    # Update analytics summary in STATE
                    STATE["analytics_summary"] = analytics.get_performance_summary()

                    # Prune old ticks once every 6 hours
                    if now_time - last_prune_time >= 21600:
                        analytics.prune_old_ticks(retention_days=3)
                        last_prune_time = now_time

                else:
                    STATE["connected"] = False
                    STATE["consecutive_failures"] += 1
                    STATE["watchdog_status"] = f"Warning: {res} (Failed {STATE['consecutive_failures']}x)"
                    logger.warning(f"Watchdog check failed: {res}")
        except Exception as e:
            logger.error(f"Watchdog loop exception: {e}")
            with STATE_LOCK:
                STATE["consecutive_failures"] += 1
                STATE["watchdog_status"] = f"Error: {e}"

        time.sleep(5)


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Nyao Scalper MT5 | Live Monitor & Analytics</title>
  <style>
    :root {
      --bg: #0b0f19;
      --card-bg: #151d30;
      --card-alt: #1a233a;
      --border: #232f48;
      --text: #f1f5f9;
      --muted: #94a3b8;
      --green: #10b981;
      --green-glow: rgba(16, 185, 129, 0.15);
      --red: #ef4444;
      --red-glow: rgba(239, 68, 68, 0.15);
      --accent: #3b82f6;
      --gold: #f59e0b;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
    body { background-color: var(--bg); color: var(--text); padding: 12px; font-size: 14px; line-height: 1.5; }
    .header { display: flex; justify-content: space-between; align-items: center; padding: 10px 4px 16px 4px; border-bottom: 1px solid var(--border); }
    .title { font-size: 18px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
    .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
    .dot.online { background-color: var(--green); box-shadow: 0 0 8px var(--green); }
    .dot.offline { background-color: var(--red); box-shadow: 0 0 8px var(--red); }
    .status-badge { font-size: 11px; padding: 3px 8px; border-radius: 12px; background: rgba(59, 130, 246, 0.15); color: #60a5fa; font-weight: 600; }
    
    .grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin: 14px 0; }
    .card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 12px; }
    .card-label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
    .card-value { font-size: 18px; font-weight: 700; }
    .card-sub { font-size: 11px; color: var(--muted); margin-top: 2px; }
    
    .analytics-panel { background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 14px; margin: 14px 0; }
    .analytics-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
    .analytics-title { font-size: 14px; font-weight: 700; color: #cbd5e1; }
    .kpi-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; text-align: center; }
    @media (max-width: 500px) {
      .kpi-row { grid-template-columns: repeat(2, 1fr); }
    }
    .kpi-item { background: var(--card-alt); padding: 8px 6px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.05); }
    .kpi-val { font-size: 16px; font-weight: 700; margin-top: 2px; }
    .kpi-lbl { font-size: 10px; color: var(--muted); text-transform: uppercase; }

    .market-ticks { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 10px; }
    .tick-box { background: var(--card-alt); padding: 8px 10px; border-radius: 8px; font-size: 12px; display: flex; justify-content: space-between; align-items: center; }

    .section-title { font-size: 14px; font-weight: 600; margin: 16px 0 8px 0; display: flex; justify-content: space-between; align-items: center; }
    .badge-count { background: #334155; padding: 2px 7px; border-radius: 10px; font-size: 11px; }
    
    .position-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 12px; margin-bottom: 8px; }
    .pos-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
    .pos-symbol { font-weight: 700; font-size: 15px; }
    .pos-type { font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 4px; text-transform: uppercase; }
    .pos-type.buy { background: var(--green-glow); color: var(--green); border: 1px solid var(--green); }
    .pos-type.sell { background: var(--red-glow); color: var(--red); border: 1px solid var(--red); }
    .pos-details { display: grid; grid-template-columns: repeat(3, 1fr); font-size: 12px; color: var(--muted); gap: 4px; margin-top: 6px; }
    .pos-profit { font-size: 16px; font-weight: 700; text-align: right; }
    
    .profit-positive { color: var(--green); }
    .profit-negative { color: var(--red); }
    
    .empty-state { text-align: center; padding: 20px; color: var(--muted); font-size: 13px; background: var(--card-bg); border-radius: 10px; border: 1px dashed var(--border); }
    .footer { text-align: center; color: var(--muted); font-size: 11px; margin-top: 24px; padding-bottom: 12px; }
  </style>
</head>
<body>
  <div class="header">
    <div class="title">
      <span id="conn-dot" class="dot offline"></span>
      <span>Nyao Scalper v54.0</span>
    </div>
    <div id="conn-status" class="status-badge">Connecting...</div>
  </div>

  <!-- Realtime Balance & Equity -->
  <div class="grid">
    <div class="card">
      <div class="card-label">Số Dư (Balance)</div>
      <div id="acc-balance" class="card-value">--</div>
      <div id="acc-balance-usd" class="card-sub">-- USD</div>
    </div>
    <div class="card">
      <div class="card-label">Tài Sản (Equity)</div>
      <div id="acc-equity" class="card-value">--</div>
      <div id="acc-equity-usd" class="card-sub">-- USD</div>
    </div>
    <div class="card">
      <div class="card-label">Lãi Lỗ Tạm Tính (Floating)</div>
      <div id="acc-floating" class="card-value">--</div>
      <div id="acc-floating-usd" class="card-sub">-- USD</div>
    </div>
    <div class="card">
      <div class="card-label">Lợi Nhuận Hôm Nay</div>
      <div id="acc-today" class="card-value">--</div>
      <div id="acc-today-usd" class="card-sub">-- USD</div>
    </div>
  </div>

  <!-- Performance Analytics & SQLite Database Engine -->
  <div class="analytics-panel">
    <div class="analytics-header">
      <div class="analytics-title">📊 Hiệu Suất PnL (Database Analytics)</div>
      <span id="stat-total-trades" class="badge-count">0 Lệnh</span>
    </div>
    <div class="kpi-row">
      <div class="kpi-item">
        <div class="kpi-lbl">Tỷ Lệ Thắng (WinRate)</div>
        <div id="kpi-winrate" class="kpi-val" style="color: var(--green);">0%</div>
      </div>
      <div class="kpi-item">
        <div class="kpi-lbl">Hệ Số Lời (Profit Factor)</div>
        <div id="kpi-pf" class="kpi-val">0.00</div>
      </div>
      <div class="kpi-item">
        <div class="kpi-lbl">Tổng Lãi Ròng (Net PnL)</div>
        <div id="kpi-net" class="kpi-val">0 USC</div>
      </div>
      <div class="kpi-item">
        <div class="kpi-lbl">Sụt Giảm (Max DD)</div>
        <div id="kpi-dd" class="kpi-val" style="color: var(--red);">0 USC</div>
      </div>
    </div>

    <!-- Live Spreads & Market Ticks -->
    <div class="market-ticks">
      <div class="tick-box">
        <div><strong>XAUUSDc</strong> <span id="tick-xau-price" style="color:#f59e0b;">--</span></div>
        <div style="font-size:11px; color:var(--muted);">Spread: <strong id="tick-xau-spd" style="color:#f1f5f9;">--</strong> pts</div>
      </div>
      <div class="tick-box">
        <div><strong>USDJPYc</strong> <span id="tick-jpy-price" style="color:#60a5fa;">--</span></div>
        <div style="font-size:11px; color:var(--muted);">Spread: <strong id="tick-jpy-spd" style="color:#f1f5f9;">--</strong> pts</div>
      </div>
    </div>
  </div>

  <!-- Open Positions -->
  <div class="section-title">
    <span>Vị Thế Đang Chạy (Open Positions)</span>
    <span id="pos-count" class="badge-count">0</span>
  </div>
  <div id="positions-container">
    <div class="empty-state">Đang tải dữ liệu...</div>
  </div>

  <!-- Closed Deals History -->
  <div class="section-title" style="margin-top: 20px;">
    <span>Lịch Sử Lệnh Đã Đóng (Closed Deals History)</span>
    <span id="deals-count" class="badge-count">0</span>
  </div>
  <div id="deals-container">
    <div class="empty-state">Chưa có dữ liệu lệnh đóng.</div>
  </div>

  <div class="footer">
    <div>Exness Real #<ACCOUNT_LOGIN> &bull; Múi giờ hiển thị: Giờ Việt Nam (GMT+7) &bull; Làm mới mỗi 5s</div>
    <div id="last-updated" style="margin-top: 4px; color: #64748b;">Cập nhật lần cuối: --</div>
  </div>

  <script>
    async function updateData() {
      try {
        const basePath = window.location.pathname.endsWith('/') ? window.location.pathname : window.location.pathname + '/';
        const res = await fetch(basePath + 'api/data');
        if (!res.ok) throw new Error("HTTP error " + res.status);
        const data = await res.json();
        
        // Header Status
        const dot = document.getElementById('conn-dot');
        const badge = document.getElementById('conn-status');
        if (data.connected) {
          dot.className = 'dot online';
          badge.textContent = 'ONLINE (' + data.account.server + ')';
          badge.style.color = '#10b981';
          badge.style.background = 'rgba(16, 185, 129, 0.15)';
        } else {
          dot.className = 'dot offline';
          badge.textContent = 'DISCONNECTED';
          badge.style.color = '#ef4444';
          badge.style.background = 'rgba(239, 68, 68, 0.15)';
        }

        const acc = data.account;
        if (acc && acc.balance !== undefined) {
          document.getElementById('acc-balance').textContent = acc.balance.toLocaleString() + ' USC';
          document.getElementById('acc-balance-usd').textContent = '≈ $' + acc.balance_usd.toFixed(2) + ' USD';
          
          document.getElementById('acc-equity').textContent = acc.equity.toLocaleString() + ' USC';
          document.getElementById('acc-equity-usd').textContent = '≈ $' + acc.equity_usd.toFixed(2) + ' USD';

          const fl = acc.floating;
          const flEl = document.getElementById('acc-floating');
          flEl.textContent = (fl >= 0 ? '+' : '') + fl.toFixed(2) + ' USC';
          flEl.className = 'card-value ' + (fl >= 0 ? 'profit-positive' : 'profit-negative');
          document.getElementById('acc-floating-usd').textContent = (fl >= 0 ? '+$' : '-$') + Math.abs(acc.floating_usd).toFixed(2) + ' USD';

          const tp = acc.today_profit;
          const tpEl = document.getElementById('acc-today');
          tpEl.textContent = (tp >= 0 ? '+' : '') + tp.toFixed(2) + ' USC';
          tpEl.className = 'card-value ' + (tp >= 0 ? 'profit-positive' : 'profit-negative');
          document.getElementById('acc-today-usd').textContent = (tp >= 0 ? '+$' : '-$') + Math.abs(acc.today_profit_usd).toFixed(2) + ' USD';
        }

        // Analytics KPI
        const an = data.analytics_summary || {};
        if (an.total_trades !== undefined) {
          document.getElementById('stat-total-trades').textContent = an.total_trades + ' Lệnh (' + an.win_trades + 'W / ' + an.loss_trades + 'L)';
          document.getElementById('kpi-winrate').textContent = an.win_rate_pct.toFixed(1) + '%';
          document.getElementById('kpi-pf').textContent = an.profit_factor >= 99 ? '∞' : an.profit_factor.toFixed(2);
          
          const netVal = an.net_profit_usc;
          const netEl = document.getElementById('kpi-net');
          netEl.textContent = (netVal >= 0 ? '+' : '') + netVal.toFixed(1) + ' USC';
          netEl.className = 'kpi-val ' + (netVal >= 0 ? 'profit-positive' : 'profit-negative');
          
          document.getElementById('kpi-dd').textContent = '-' + an.max_drawdown_usc.toFixed(1) + ' USC';
        }

        // Live Ticks & Spreads
        const ticks = data.latest_ticks || {};
        if (ticks['XAUUSDc']) {
          document.getElementById('tick-xau-price').textContent = ticks['XAUUSDc'].bid.toFixed(2);
          document.getElementById('tick-xau-spd').textContent = ticks['XAUUSDc'].spread_points;
        }
        if (ticks['USDJPYc']) {
          document.getElementById('tick-jpy-price').textContent = ticks['USDJPYc'].bid.toFixed(3);
          document.getElementById('tick-jpy-spd').textContent = ticks['USDJPYc'].spread_points;
        }

        // Open Positions
        const posContainer = document.getElementById('positions-container');
        const positions = data.positions || [];
        document.getElementById('pos-count').textContent = positions.length;

        if (positions.length === 0) {
          posContainer.innerHTML = '<div class="empty-state">Không có lệnh nào đang chạy.</div>';
        } else {
          let html = '';
          positions.forEach(p => {
            const isBuy = p.type === 'BUY';
            const profitClass = p.profit_cent >= 0 ? 'profit-positive' : 'profit-negative';
            const profitPrefix = p.profit_cent >= 0 ? '+' : '';
            html += `
              <div class="position-card">
                <div class="pos-header">
                  <div>
                    <span class="pos-symbol">${p.symbol}</span>
                    <span class="pos-type ${isBuy ? 'buy' : 'sell'}">${p.type} ${p.volume} lot</span>
                  </div>
                  <div class="pos-profit ${profitClass}">
                    ${profitPrefix}${p.profit_cent.toFixed(2)} USC
                    <div style="font-size: 11px; font-weight: normal; color: var(--muted);">${profitPrefix}$${p.profit_usd.toFixed(4)}</div>
                  </div>
                </div>
                <div class="pos-details">
                  <div>Vào: <strong>${p.price_open}</strong></div>
                  <div>Hiện tại: <strong>${p.price_current}</strong></div>
                  <div>Giờ mở: <strong>${p.time} (VN)</strong> <span style="font-size:10px; color:#64748b;">[${p.time_server} Exness]</span></div>
                  <div>SL: <strong>${p.sl > 0 ? p.sl : 'None'}</strong></div>
                  <div>TP: <strong>${p.tp > 0 ? p.tp : 'None'}</strong></div>
                  <div>Mã vé: <strong>#${p.ticket}</strong></div>
                </div>
              </div>
            `;
          });
          posContainer.innerHTML = html;
        }

        // Recent Closed Deals from Database
        const dealsContainer = document.getElementById('deals-container');
        const deals = data.recent_deals || data.today_deals || [];
        document.getElementById('deals-count').textContent = deals.length;
        if (deals.length === 0) {
          dealsContainer.innerHTML = '<div class="empty-state">Chưa có lịch sử lệnh đóng.</div>';
        } else {
          let html = '';
          deals.forEach(d => {
            const profitClass = d.profit_cent >= 0 ? 'profit-positive' : 'profit-negative';
            const profitPrefix = d.profit_cent >= 0 ? '+' : '';
            const t = d.deal_time_vn || d.time;
            html += `
              <div class="position-card" style="padding: 10px;">
                <div class="pos-header">
                  <div>
                    <span style="font-weight: 600;">${d.symbol}</span>
                    <span style="font-size: 11px; color: var(--muted); margin-left: 6px;">${d.trade_type || d.type} ${d.volume} lot @ ${d.price}</span>
                  </div>
                  <div class="${profitClass}" style="font-weight: 700;">
                    ${profitPrefix}${d.profit_cent.toFixed(2)} USC
                  </div>
                </div>
                <div style="font-size: 11px; color: #64748b; margin-top: 2px;">Thời gian: <strong>${t} (VN)</strong> &bull; Ticket #${d.deal_ticket || d.ticket}</div>
              </div>
            `;
          });
          dealsContainer.innerHTML = html;
        }

        document.getElementById('last-updated').textContent = 'Cập nhật lần cuối: ' + new Date().toLocaleTimeString();
      } catch (err) {
        console.error("Dashboard update error:", err);
      }
    }

    updateData();
    setInterval(updateData, 5000);
  </script>
</body>
</html>
"""


class DashboardHTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        clean_path = self.path.split("?")[0].rstrip("/")
        if clean_path in ("", "/index.html", "/dashboard"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
        elif clean_path.endswith("/api/data"):
            with STATE_LOCK:
                recent_deals = analytics.get_recent_closed_deals(limit=50)
                resp = {
                    "connected": STATE["connected"],
                    "watchdog_status": STATE["watchdog_status"],
                    "last_check": STATE["last_check"],
                    "account": STATE["account"],
                    "positions": STATE["positions"],
                    "today_deals": STATE["today_deals"],
                    "analytics_summary": STATE["analytics_summary"],
                    "latest_ticks": STATE["latest_ticks"],
                    "recent_deals": recent_deals
                }
            payload = json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)
        elif clean_path.endswith("/api/analytics"):
            summary = analytics.get_performance_summary()
            equity_curve = analytics.get_equity_curve(limit=100)
            recent_deals = analytics.get_recent_closed_deals(limit=50)
            payload = json.dumps({
                "summary": summary,
                "equity_curve": equity_curve,
                "recent_deals": recent_deals
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)
        elif clean_path.endswith("/health"):
            with STATE_LOCK:
                st = {
                    "status": "UP" if STATE["connected"] else "DOWN",
                    "watchdog": STATE["watchdog_status"],
                    "failures": STATE["consecutive_failures"],
                    "last_check": STATE["last_check"]
                }
            self.send_response(200 if STATE["connected"] else 503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(st).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Silence access logs to keep terminal quiet
        return


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def run_server(port=8001):
    server = ThreadedHTTPServer(("0.0.0.0", port), DashboardHTTPHandler)
    logger.info(f"Dashboard Web Server listening on http://0.0.0.0:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    t = threading.Thread(target=watchdog_worker, daemon=True)
    t.start()
    run_server(8001)
