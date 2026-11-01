#!/usr/bin/env python3
"""
Nyao Scalper MT5 - Automated Daily & Monthly Trade Reporting Service
Extracts execution history directly from MetaTrader 5 via Python API
and dispatches structured HTML financial performance reports via SMTP.
"""

import os
import sys
import json
import argparse
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

# Try importing MetaTrader5 (available in Windows Python / Wine)
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False


def load_config(config_path="config.json"):
    """Load configuration file, falling back to environment variables."""
    config = {
        "smtp": {
            "server": os.getenv("SMTP_SERVER", "smtp.gmail.com"),
            "port": int(os.getenv("SMTP_PORT", 587)),
            "use_tls": os.getenv("SMTP_USE_TLS", "true").lower() in ("true", "1"),
            "username": os.getenv("SMTP_USERNAME", ""),
            "password": os.getenv("SMTP_PASSWORD", "")
        },
        "email": {
            "sender_name": os.getenv("EMAIL_SENDER_NAME", "Nyao Scalper VPS Bot"),
            "sender_email": os.getenv("EMAIL_SENDER_EMAIL", ""),
            "recipients": [r.strip() for r in os.getenv("EMAIL_RECIPIENTS", "").split(",") if r.strip()]
        },
        "timezone": os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh"),
        "mt5_path": os.getenv("MT5_PATH", r"C:\Program Files\MetaTrader 5\terminal64.exe")
    }

    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
                if "smtp" in user_cfg:
                    config["smtp"].update(user_cfg["smtp"])
                if "email" in user_cfg:
                    config["email"].update(user_cfg["email"])
                if "timezone" in user_cfg:
                    config["timezone"] = user_cfg["timezone"]
                if "mt5_path" in user_cfg:
                    config["mt5_path"] = user_cfg["mt5_path"]
        except Exception as e:
            print(f"[WARN] Error reading {config_path}: {e}")

    return config


def init_mt5(mt5_path):
    """Initialize MT5 connection."""
    if not MT5_AVAILABLE:
        print("[WARN] MetaTrader5 module not installed in current Python environment.")
        return False

    if not mt5.initialize(path=mt5_path):
        # Fallback to default running instance
        if not mt5.initialize():
            print(f"[ERROR] mt5.initialize() failed: {mt5.last_error()}")
            return False

    return True


def fetch_account_info():
    """Retrieve current account status."""
    if not MT5_AVAILABLE:
        return {}

    acc = mt5.account_info()
    if acc is None:
        return {}

    return {
        "login": acc.login,
        "name": acc.name,
        "server": acc.server,
        "currency": acc.currency,
        "balance": acc.balance,
        "equity": acc.equity,
        "margin": acc.margin,
        "margin_free": acc.margin_free,
        "margin_level": acc.margin_level,
        "profit": acc.profit,
        "leverage": acc.leverage
    }


def fetch_open_positions(tz):
    """Retrieve list of currently open positions."""
    if not MT5_AVAILABLE:
        return []

    positions = mt5.positions_get()
    if positions is None or len(positions) == 0:
        return []

    res = []
    for p in positions:
        open_time = datetime.fromtimestamp(p.time, tz=timezone.utc).astimezone(tz)
        res.append({
            "ticket": p.ticket,
            "symbol": p.symbol,
            "type": "BUY" if p.type == 0 else "SELL",
            "volume": p.volume,
            "price_open": p.price_open,
            "price_current": p.price_current,
            "sl": p.sl,
            "tp": p.tp,
            "profit": p.profit,
            "swap": p.swap,
            "time": open_time.strftime("%Y-%m-%d %H:%M:%S"),
            "comment": p.comment
        })
    return res


def fetch_deals_history(from_date, to_date, tz):
    """Retrieve and aggregate closed trade deals within date range."""
    if not MT5_AVAILABLE:
        return {"summary": {}, "deals": [], "symbols": {}}

    deals = mt5.history_deals_get(from_date, to_date)
    if deals is None or len(deals) == 0:
        return {"summary": {}, "deals": [], "symbols": {}}

    closed_deals = []
    total_profit = 0.0
    total_commission = 0.0
    total_swap = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    wins = 0
    losses = 0
    symbol_stats = {}

    for d in deals:
        # We only care about exit deals (DEAL_ENTRY_OUT or DEAL_ENTRY_INOUT)
        # 1 = DEAL_ENTRY_OUT, 2 = DEAL_ENTRY_INOUT
        if d.entry not in (1, 2) or d.symbol == "":
            continue

        deal_profit = d.profit
        deal_comm = d.commission
        deal_swap = d.swap
        net_deal_profit = deal_profit + deal_comm + deal_swap

        total_profit += net_deal_profit
        total_commission += deal_comm
        total_swap += deal_swap

        if net_deal_profit > 0:
            wins += 1
            gross_profit += net_deal_profit
        elif net_deal_profit < 0:
            losses += 1
            gross_loss += abs(net_deal_profit)

        # Symbol breakdown
        sym = d.symbol
        if sym not in symbol_stats:
            symbol_stats[sym] = {"trades": 0, "profit": 0.0, "volume": 0.0, "wins": 0}
        symbol_stats[sym]["trades"] += 1
        symbol_stats[sym]["profit"] += net_deal_profit
        symbol_stats[sym]["volume"] += d.volume
        if net_deal_profit > 0:
            symbol_stats[sym]["wins"] += 1

        deal_time = datetime.fromtimestamp(d.time, tz=timezone.utc).astimezone(tz)
        closed_deals.append({
            "ticket": d.ticket,
            "order": d.order,
            "time": deal_time.strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": d.symbol,
            "type": "BUY" if d.type == 0 else "SELL",
            "volume": d.volume,
            "price": d.price,
            "profit": net_deal_profit,
            "raw_profit": deal_profit,
            "commission": deal_comm,
            "swap": deal_swap,
            "comment": d.comment
        })

    total_trades = wins + losses
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

    summary = {
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "net_profit": total_profit,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "commission": total_commission,
        "swap": total_swap
    }

    return {
        "summary": summary,
        "deals": closed_deals,
        "symbols": symbol_stats
    }


def render_html_report(report_type, account, data, open_positions, start_dt, end_dt, tz_name):
    """Generate professional, anti-AI styled HTML report with clear financial metrics."""
    summary = data.get("summary", {})
    deals = data.get("deals", [])
    symbols = data.get("symbols", {})

    net_profit = summary.get("net_profit", 0.0)
    profit_color = "#10b981" if net_profit >= 0 else "#ef4444"
    profit_sign = "+" if net_profit > 0 else ""
    currency = account.get("currency", "USD")

    title_label = "DAILY TRADE REPORT" if report_type == "daily" else "MONTHLY TRADE RECAP"
    date_str = start_dt.strftime("%d/%m/%Y") if report_type == "daily" else f"{start_dt.strftime('%B %Y')}"
    period_str = f"{start_dt.strftime('%Y-%m-%d %H:%M')} &mdash; {end_dt.strftime('%Y-%m-%d %H:%M')} ({tz_name})"

    # Symbol breakdown HTML
    symbols_html = ""
    if symbols:
        symbols_html = """
        <h3 style="margin-top: 28px; margin-bottom: 12px; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px; color: #475569;">Performance by Symbol</h3>
        <table style="width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; margin-bottom: 24px;">
            <thead>
                <tr style="background: #f1f5f9; color: #334155; border-bottom: 2px solid #e2e8f0;">
                    <th style="padding: 10px 12px;">Symbol</th>
                    <th style="padding: 10px 12px; text-align: right;">Trades</th>
                    <th style="padding: 10px 12px; text-align: right;">Volume (Lots)</th>
                    <th style="padding: 10px 12px; text-align: right;">Win Rate</th>
                    <th style="padding: 10px 12px; text-align: right;">Net P&L</th>
                </tr>
            </thead>
            <tbody>
        """
        for sym, sdata in symbols.items():
            sym_wr = (sdata["wins"] / sdata["trades"] * 100) if sdata["trades"] > 0 else 0.0
            sym_prof = sdata["profit"]
            sym_color = "#10b981" if sym_prof >= 0 else "#ef4444"
            sym_sign = "+" if sym_prof > 0 else ""
            symbols_html += f"""
                <tr style="border-bottom: 1px solid #f1f5f9;">
                    <td style="padding: 10px 12px; font-weight: 600; color: #1e293b;">{sym}</td>
                    <td style="padding: 10px 12px; text-align: right; color: #475569;">{sdata['trades']}</td>
                    <td style="padding: 10px 12px; text-align: right; color: #475569;">{sdata['volume']:.2f}</td>
                    <td style="padding: 10px 12px; text-align: right; color: #475569;">{sym_wr:.1f}%</td>
                    <td style="padding: 10px 12px; text-align: right; font-weight: 600; color: {sym_color};">{sym_sign}{sym_prof:.2f} {currency}</td>
                </tr>
            """
        symbols_html += "</tbody></table>"

    # Deals table HTML
    deals_html = ""
    if deals:
        deals_html = """
        <h3 style="margin-top: 28px; margin-bottom: 12px; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px; color: #475569;">Closed Deals Details</h3>
        <div style="overflow-x: auto;">
        <table style="width: 100%; border-collapse: collapse; font-size: 12px; text-align: left; margin-bottom: 24px;">
            <thead>
                <tr style="background: #f1f5f9; color: #334155; border-bottom: 2px solid #e2e8f0;">
                    <th style="padding: 8px 10px;">Time</th>
                    <th style="padding: 8px 10px;">Ticket</th>
                    <th style="padding: 8px 10px;">Symbol</th>
                    <th style="padding: 8px 10px;">Type</th>
                    <th style="padding: 8px 10px; text-align: right;">Lots</th>
                    <th style="padding: 8px 10px; text-align: right;">Exit Price</th>
                    <th style="padding: 8px 10px; text-align: right;">P&L</th>
                    <th style="padding: 8px 10px;">Comment</th>
                </tr>
            </thead>
            <tbody>
        """
        for d in deals:
            d_prof = d["profit"]
            d_color = "#10b981" if d_prof >= 0 else "#ef4444"
            d_sign = "+" if d_prof > 0 else ""
            type_bg = "#dcfce7" if d["type"] == "BUY" else "#fee2e2"
            type_color = "#15803d" if d["type"] == "BUY" else "#b91c1c"
            deals_html += f"""
                <tr style="border-bottom: 1px solid #f1f5f9;">
                    <td style="padding: 8px 10px; color: #64748b; white-space: nowrap;">{d['time']}</td>
                    <td style="padding: 8px 10px; color: #475569; font-family: monospace;">#{d['ticket']}</td>
                    <td style="padding: 8px 10px; font-weight: 600; color: #1e293b;">{d['symbol']}</td>
                    <td style="padding: 8px 10px;"><span style="background: {type_bg}; color: {type_color}; padding: 2px 6px; border-radius: 4px; font-weight: 600; font-size: 11px;">{d['type']}</span></td>
                    <td style="padding: 8px 10px; text-align: right; color: #475569;">{d['volume']:.2f}</td>
                    <td style="padding: 8px 10px; text-align: right; font-family: monospace; color: #334155;">{d['price']:.2f}</td>
                    <td style="padding: 8px 10px; text-align: right; font-weight: 600; color: {d_color};">{d_sign}{d_prof:.2f}</td>
                    <td style="padding: 8px 10px; color: #94a3b8; font-size: 11px;">{d['comment']}</td>
                </tr>
            """
        deals_html += "</tbody></table></div>"
    else:
        deals_html = "<div style='padding: 16px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; color: #64748b; font-size: 13px; margin-bottom: 24px;'>No closed trades during this period.</div>"

    # Open positions HTML
    open_html = ""
    if open_positions:
        open_html = """
        <h3 style="margin-top: 28px; margin-bottom: 12px; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px; color: #475569;">Currently Open Positions</h3>
        <table style="width: 100%; border-collapse: collapse; font-size: 12px; text-align: left; margin-bottom: 24px;">
            <thead>
                <tr style="background: #f1f5f9; color: #334155; border-bottom: 2px solid #e2e8f0;">
                    <th style="padding: 8px 10px;">Symbol</th>
                    <th style="padding: 8px 10px;">Type</th>
                    <th style="padding: 8px 10px; text-align: right;">Lots</th>
                    <th style="padding: 8px 10px; text-align: right;">Open Price</th>
                    <th style="padding: 8px 10px; text-align: right;">Current Price</th>
                    <th style="padding: 8px 10px; text-align: right;">Floating P&L</th>
                </tr>
            </thead>
            <tbody>
        """
        for op in open_positions:
            op_prof = op["profit"]
            op_color = "#10b981" if op_prof >= 0 else "#ef4444"
            op_sign = "+" if op_prof > 0 else ""
            type_bg = "#dcfce7" if op["type"] == "BUY" else "#fee2e2"
            type_color = "#15803d" if op["type"] == "BUY" else "#b91c1c"
            open_html += f"""
                <tr style="border-bottom: 1px solid #f1f5f9;">
                    <td style="padding: 8px 10px; font-weight: 600; color: #1e293b;">{op['symbol']}</td>
                    <td style="padding: 8px 10px;"><span style="background: {type_bg}; color: {type_color}; padding: 2px 6px; border-radius: 4px; font-weight: 600; font-size: 11px;">{op['type']}</span></td>
                    <td style="padding: 8px 10px; text-align: right; color: #475569;">{op['volume']:.2f}</td>
                    <td style="padding: 8px 10px; text-align: right; font-family: monospace;">{op['price_open']:.2f}</td>
                    <td style="padding: 8px 10px; text-align: right; font-family: monospace;">{op['price_current']:.2f}</td>
                    <td style="padding: 8px 10px; text-align: right; font-weight: 600; color: {op_color};">{op_sign}{op_prof:.2f}</td>
                </tr>
            """
        open_html += "</tbody></table>"

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>{title_label} - {date_str}</title>
    </head>
    <body style="margin: 0; padding: 24px; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b;">
        <div style="max-width: 680px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
            <!-- Header -->
            <div style="background: #0f172a; padding: 24px 28px; color: #ffffff;">
                <div style="font-size: 12px; font-weight: 700; letter-spacing: 1.5px; color: #38bdf8; text-transform: uppercase;">NYAO SCALPER &bull; SINGAPORE VPS</div>
                <h1 style="margin: 6px 0 4px 0; font-size: 22px; font-weight: 700;">{title_label}</h1>
                <div style="font-size: 13px; color: #94a3b8;">{period_str}</div>
            </div>

            <div style="padding: 24px 28px;">
                <!-- Account Meta Bar -->
                <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 12px 16px; margin-bottom: 24px; font-size: 13px; display: flex; justify-content: space-between; flex-wrap: wrap;">
                    <div><strong>Account:</strong> #{account.get('login', 'N/A')} ({account.get('server', 'N/A')})</div>
                    <div><strong>Equity:</strong> {account.get('equity', 0.0):,.2f} {currency} &bull; <strong>Balance:</strong> {account.get('balance', 0.0):,.2f} {currency}</div>
                </div>

                <!-- KPI Cards Grid -->
                <table style="width: 100%; border-collapse: separate; border-spacing: 10px 0; margin-left: -10px; margin-right: -10px; margin-bottom: 24px;">
                    <tr>
                        <td style="width: 25%; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 16px; text-align: center;">
                            <div style="font-size: 11px; text-transform: uppercase; color: #64748b; font-weight: 600; letter-spacing: 0.5px;">Net Profit</div>
                            <div style="font-size: 20px; font-weight: 700; color: {profit_color}; margin-top: 4px;">{profit_sign}{net_profit:.2f}</div>
                            <div style="font-size: 11px; color: #94a3b8;">{currency}</div>
                        </td>
                        <td style="width: 25%; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 16px; text-align: center;">
                            <div style="font-size: 11px; text-transform: uppercase; color: #64748b; font-weight: 600; letter-spacing: 0.5px;">Win Rate</div>
                            <div style="font-size: 20px; font-weight: 700; color: #0284c7; margin-top: 4px;">{summary.get('win_rate', 0.0):.1f}%</div>
                            <div style="font-size: 11px; color: #64748b;">{summary.get('wins', 0)}W / {summary.get('losses', 0)}L</div>
                        </td>
                        <td style="width: 25%; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 16px; text-align: center;">
                            <div style="font-size: 11px; text-transform: uppercase; color: #64748b; font-weight: 600; letter-spacing: 0.5px;">Profit Factor</div>
                            <div style="font-size: 20px; font-weight: 700; color: #0284c7; margin-top: 4px;">{summary.get('profit_factor', 0.0):.2f}</div>
                            <div style="font-size: 11px; color: #94a3b8;">Gross W/L</div>
                        </td>
                        <td style="width: 25%; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 16px; text-align: center;">
                            <div style="font-size: 11px; text-transform: uppercase; color: #64748b; font-weight: 600; letter-spacing: 0.5px;">Total Trades</div>
                            <div style="font-size: 20px; font-weight: 700; color: #1e293b; margin-top: 4px;">{summary.get('total_trades', 0)}</div>
                            <div style="font-size: 11px; color: #94a3b8;">Closed Orders</div>
                        </td>
                    </tr>
                </table>

                {symbols_html}
                {deals_html}
                {open_html}

                <!-- Footer -->
                <div style="margin-top: 32px; padding-top: 16px; border-top: 1px solid #f1f5f9; font-size: 11px; color: #94a3b8; text-align: center;">
                    Generated automatically by Nyao Scalper Reporting Engine &bull; Host: <VPS_IP> (asia-southeast1-c) &bull; Latency: ~1.5ms
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return html


def send_email_report(subject, html_content, config):
    """Dispatch email via configured SMTP server."""
    smtp_cfg = config["smtp"]
    email_cfg = config["email"]

    if not smtp_cfg.get("username") or not smtp_cfg.get("password"):
        print("[ERROR] SMTP username or password not configured in config.json or environment.")
        return False

    recipients = email_cfg.get("recipients", [])
    if not recipients:
        print("[ERROR] No recipients configured in config.json or EMAIL_RECIPIENTS.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{email_cfg.get('sender_name', 'Nyao Scalper')} <{smtp_cfg['username']}>"
    msg["To"] = ", ".join(recipients)

    part = MIMEText(html_content, "html", "utf-8")
    msg.attach(part)

    try:
        print(f"[INFO] Connecting to SMTP server {smtp_cfg['server']}:{smtp_cfg['port']}...")
        if smtp_cfg.get("port") == 465:
            server = smtplib.SMTP_SSL(smtp_cfg["server"], smtp_cfg["port"], timeout=15)
        else:
            server = smtplib.SMTP(smtp_cfg["server"], smtp_cfg["port"], timeout=15)
            if smtp_cfg.get("use_tls", True):
                server.starttls()

        server.login(smtp_cfg["username"], smtp_cfg["password"])
        server.sendmail(smtp_cfg["username"], recipients, msg.as_string())
        server.quit()
        print(f"[SUCCESS] Report sent to {', '.join(recipients)}.")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to send email via SMTP: {e}")
        return False


def run_report(report_type="daily", config_path="config.json", save_html_path=None, send_mail=True):
    """Main reporting workflow."""
    config = load_config(config_path)
    tz = ZoneInfo(config.get("timezone", "Asia/Ho_Chi_Minh"))
    now = datetime.now(tz)

    if report_type == "daily":
        # Today from 00:00:00 to now (or previous 24h)
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
        subject_label = f"Daily Report - {start_dt.strftime('%d/%m/%Y')}"
    elif report_type == "monthly":
        # First day of current month to now
        start_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
        subject_label = f"Monthly Recap - {start_dt.strftime('%B %Y')}"
    elif report_type == "yesterday":
        yesterday = now - timedelta(days=1)
        start_dt = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)
        subject_label = f"Daily Report - {start_dt.strftime('%d/%m/%Y')}"
    else:
        raise ValueError(f"Unknown report_type: {report_type}")

    print(f"[INFO] Generating {report_type} report for period: {start_dt} -> {end_dt} ({config['timezone']})")

    # Connect to MT5
    mt5_ok = init_mt5(config["mt5_path"])
    account = {}
    data = {"summary": {}, "deals": [], "symbols": {}}
    open_positions = []

    if mt5_ok:
        account = fetch_account_info()
        data = fetch_deals_history(start_dt, end_dt, tz)
        open_positions = fetch_open_positions(tz)
        mt5.shutdown()
    else:
        print("[WARN] Proceeding with placeholder account data (MT5 offline or non-Windows host).")
        account = {
            "login": 464158177,
            "server": "Exness-MT5Trial",
            "currency": "USD",
            "balance": 1000.0,
            "equity": 1000.0
        }

    html = render_html_report(report_type, account, data, open_positions, start_dt, end_dt, config["timezone"])

    if save_html_path:
        with open(save_html_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[INFO] HTML report saved to {save_html_path}")

    if send_mail:
        login_str = f" #{account.get('login', '')}" if account.get('login') else ""
        subject = f"[Nyao Scalper]{login_str} {subject_label}"
        send_email_report(subject, html, config)

    return html


def main():
    parser = argparse.ArgumentParser(description="Nyao Scalper MT5 Email Reporting Service")
    parser.add_argument("--type", choices=["daily", "yesterday", "monthly"], default="daily", help="Report period")
    parser.add_argument("--config", default="config.json", help="Path to config.json")
    parser.add_argument("--save-html", help="Path to write rendered HTML")
    parser.add_argument("--no-mail", action="store_true", help="Generate HTML without sending email")
    parser.add_argument("--test", action="store_true", help="Send test email with current snapshot")
    args = parser.parse_args()

    run_report(
        report_type=args.type,
        config_path=args.config,
        save_html_path=args.save_html,
        send_mail=not args.no_mail
    )


if __name__ == "__main__":
    main()

