"""
Nyao Scalper - Live Mobile Web Dashboard & Watchdog Service
Runs inside MT5 Wine/Python environment, serving on port 8001.
Zero external dependencies: uses Python standard library http.server + threading.
"""

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("watchdog.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("WatchdogService")

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
}

STATE_LOCK = threading.Lock()


def get_mt5_snapshot():
    """Queries MT5 API for real-time account, positions, and today's closed deals."""
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
            positions.append({
                "ticket": p.ticket,
                "time": datetime.fromtimestamp(p.time).strftime("%H:%M:%S"),
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

    # Today's deals
    now = datetime.now()
    start_of_day = datetime(now.year, now.month, now.day, 0, 0, 0)
    raw_deals = mt5.history_deals_get(start_of_day, now)
    today_deals = []
    today_profit = 0.0
    if raw_deals:
        for d in raw_deals:
            # entry == 1 means DEAL_ENTRY_OUT (position closed)
            if d.entry == 1:
                today_profit += d.profit
                today_deals.append({
                    "ticket": d.ticket,
                    "order": d.order,
                    "time": datetime.fromtimestamp(d.time).strftime("%H:%M:%S"),
                    "symbol": d.symbol,
                    "type": "BUY" if d.type == 0 else "SELL",
                    "volume": round(d.volume, 2),
                    "price": round(d.price, 3),
                    "profit_cent": round(d.profit, 2),
                    "profit_usd": round(d.profit / 100.0, 4) if "c" in d.symbol.lower() else round(d.profit, 2)
                })

    is_cent = "c" in acc.currency.lower() or "usc" in acc.currency.lower()
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
        "ping": term_info.ping_last / 1000.0 if hasattr(term_info, "ping_last") else 0
    }
    return True, data


def watchdog_worker():
    """Background thread polling MT5 status every 30 seconds."""
    logger.info("Watchdog engine started.")
    while True:
        try:
            ok, res = get_mt5_snapshot()
            with STATE_LOCK:
                STATE["last_check"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if ok:
                    STATE["connected"] = True
                    STATE["account"] = res
                    STATE["positions"] = res["positions"]
                    STATE["today_deals"] = res["today_deals"]
                    STATE["today_profit"] = res["today_profit"]
                    STATE["consecutive_failures"] = 0
                    STATE["watchdog_status"] = "Healthy (Connected)"
                else:
                    STATE["connected"] = False
                    STATE["consecutive_failures"] += 1
                    STATE["watchdog_status"] = f"Warning: {res} (Failed {STATE['consecutive_failures']}x)"
                    logger.warning(f"Watchdog check failed: {res}")
                    if STATE["consecutive_failures"] >= 6:
                        # 3 minutes of consecutive failures
                        logger.error("CRITICAL: MT5 has been disconnected for > 3 minutes!")
                        STATE["watchdog_status"] = "CRITICAL: Disconnected > 3 min"
        except Exception as e:
            logger.error(f"Watchdog loop exception: {e}")
            with STATE_LOCK:
                STATE["consecutive_failures"] += 1
                STATE["watchdog_status"] = f"Error: {e}"

        time.sleep(10)


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Nyao Scalper MT5 | Live Monitor</title>
  <style>
    :root {
      --bg: #0b0f19;
      --card-bg: #151d30;
      --border: #232f48;
      --text: #f1f5f9;
      --muted: #94a3b8;
      --green: #10b981;
      --green-glow: rgba(16, 185, 129, 0.15);
      --red: #ef4444;
      --red-glow: rgba(239, 68, 68, 0.15);
      --accent: #3b82f6;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
    body { background-color: var(--bg); color: var(--text); padding: 12px; font-size: 14px; line-height: 1.5; }
    .header { display: flex; justify-content: space-between; align-items: center; padding: 10px 4px 16px 4px; border-bottom: 1px solid var(--border); }
    .title { font-size: 18px; font-weight: 700; display: flex; align-items: center; gap: 8px; }
    .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
    .dot.online { background-color: var(--green); box-shadow: 0 0 8px var(--green); }
    .dot.offline { background-color: var(--red); box-shadow: 0 0 8px var(--red); }
    .status-badge { font-size: 11px; padding: 3px 8px; border-radius: 12px; background: rgba(59, 130, 246, 0.15); color: #60a5fa; font-weight: 600; }
    
    .grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin: 16px 0; }
    .card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 12px; }
    .card-label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
    .card-value { font-size: 19px; font-weight: 700; }
    .card-sub { font-size: 11px; color: var(--muted); margin-top: 2px; }
    
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
    
    .empty-state { text-align: center; padding: 24px; color: var(--muted); font-size: 13px; background: var(--card-bg); border-radius: 10px; border: 1px dashed var(--border); }
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
      <div class="card-label">Lãi Lỗ Tạm Tính</div>
      <div id="acc-floating" class="card-value">--</div>
      <div id="acc-floating-usd" class="card-sub">-- USD</div>
    </div>
    <div class="card">
      <div class="card-label">Lợi Nhuận Hôm Nay</div>
      <div id="acc-today" class="card-value">--</div>
      <div id="acc-today-usd" class="card-sub">-- USD</div>
    </div>
  </div>

  <div class="section-title">
    <span>Vị Thế Đang Chạy (Open Positions)</span>
    <span id="pos-count" class="badge-count">0</span>
  </div>
  <div id="positions-container">
    <div class="empty-state">Đang tải dữ liệu...</div>
  </div>

  <div class="section-title" style="margin-top: 20px;">
    <span>Lệnh Đã Đóng Hôm Nay (Closed Deals)</span>
    <span id="deals-count" class="badge-count">0</span>
  </div>
  <div id="deals-container">
    <div class="empty-state">Chưa có lệnh đóng hôm nay.</div>
  </div>

  <div class="footer">
    <div>Exness Real #<ACCOUNT_LOGIN> &bull; Tự động cập nhật mỗi 5 giây</div>
    <div id="last-updated" style="margin-top: 4px; color: #64748b;">Cập nhật lần cuối: --</div>
  </div>

  <script>
    async function updateData() {
      try {
        const res = await fetch('/api/data');
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
                  <div>Giờ mở: <strong>${p.time}</strong></div>
                  <div>SL: <strong>${p.sl > 0 ? p.sl : 'None'}</strong></div>
                  <div>TP: <strong>${p.tp > 0 ? p.tp : 'None'}</strong></div>
                  <div>Mã vé: <strong>#${p.ticket}</strong></div>
                </div>
              </div>
            `;
          });
          posContainer.innerHTML = html;
        }

        // Deals today
        const dealsContainer = document.getElementById('deals-container');
        const deals = data.today_deals || [];
        document.getElementById('deals-count').textContent = deals.length;
        if (deals.length === 0) {
          dealsContainer.innerHTML = '<div class="empty-state">Chưa có lệnh nào đóng hôm nay.</div>';
        } else {
          let html = '';
          deals.slice().reverse().forEach(d => {
            const profitClass = d.profit_cent >= 0 ? 'profit-positive' : 'profit-negative';
            const profitPrefix = d.profit_cent >= 0 ? '+' : '';
            html += `
              <div class="position-card" style="padding: 10px;">
                <div class="pos-header">
                  <div>
                    <span style="font-weight: 600;">${d.symbol}</span>
                    <span style="font-size: 11px; color: var(--muted); margin-left: 6px;">${d.type} ${d.volume} lot @ ${d.price}</span>
                  </div>
                  <div class="${profitClass}" style="font-weight: 700;">
                    ${profitPrefix}${d.profit_cent.toFixed(2)} USC
                  </div>
                </div>
                <div style="font-size: 11px; color: #64748b; margin-top: 2px;">Đóng lúc: ${d.time} &bull; Order #${d.order}</div>
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
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
        elif self.path == "/api/data":
            with STATE_LOCK:
                resp = {
                    "connected": STATE["connected"],
                    "watchdog_status": STATE["watchdog_status"],
                    "last_check": STATE["last_check"],
                    "account": STATE["account"],
                    "positions": STATE["positions"],
                    "today_deals": STATE["today_deals"]
                }
            payload = json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)
        elif self.path == "/health":
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
