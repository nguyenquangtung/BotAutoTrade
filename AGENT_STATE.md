# Multi-Agent State & Handover Log (AGENT_STATE.md)

## 1. Trạng Thái Hiện Tại (Current Snapshot)
- **Thời điểm cập nhật:** 2026-11-03 09:10 (GMT+7, Vietnam)
- **Agent cập nhật gần nhất:** Antigravity (Google DeepMind)
- **Mục tiêu sản phẩm / Task chính:** Hệ thống giao dịch tự động MetaTrader 5 (Nyao Scalper v54.0) chạy 2 cặp `XAUUSDc` và `USDJPYc` trên tài khoản Cent Exness (`#<ACCOUNT_LOGIN>`), tích hợp Web Monitor di động, Engine phân tích PnL/Tick SQLite và Watchdog tự phục hồi trên Google Cloud VPS Singapore.
- **Trạng thái Repo / Branch:** Branch `main`, Commit hash `32e8582`.
- **Trạng thái VPS Production:**
  - Host VPS: Google Cloud `<VPS_IP>` (`asia-southeast1-c`), User `<VPS_USER>`.
  - Container: `mt5_trading` (WineHQ Staging, MetaTrader 5 Build 4452+).
  - Web Monitor: `http://<VPS_IP>:3000/dashboard/` (Basic Auth: `admin` / `<REDACTED>`).
  - Account: `1,872.30 USC` (~$18.72 USD), 0 open positions, MT5 Ping `5.63 ms`.

---

## 2. Việc Đã Hoàn Thành (Completed Tasks)
- [x] **EA Nyao Scalper v54.0**: Vận hành song song 2 biểu đồ M5 trên MT5 (`XAUUSDc` Magic `6926268`, `USDJPYc` Magic `6926270`).
- [x] **Docker MT5 VPS Deployment**: Thiết lập container `mt5_trading`, cấu hình Wine GUI và tự động khởi động MT5 terminal.
- [x] **Live Mobile Web Dashboard (`dashboard/service.py`)**: Giao diện dark mode di động, tự động làm mới mỗi 5 giây, hiển thị Balance, Equity, Floating PnL, Open Positions.
- [x] **Timezone Localization (GMT+7)**: Toàn bộ mốc thời gian hiển thị trên Web Dashboard đã được chuyển đổi chính xác sang Giờ Việt Nam, có chú thích Giờ Exness Server (GMT+0).
- [x] **Database PnL & Tick History Collector (`dashboard/analytics_db.py`)**:
  - Tích hợp SQLite3 persistent tại `dashboard/data/trading_analytics.db`.
  - Bảng `account_snapshots`: Lưu lịch sử vốn mỗi 60 giây phục vụ vẽ Equity Curve và đo Max Drawdown.
  - Bảng `closed_deals`: Tự động đồng bộ toàn bộ lịch sử lệnh chốt (`DEAL_ENTRY_OUT`).
  - Bảng `tick_history`: Ghi nhận tick và đo độ giãn spread thực tế cho `XAUUSDc` và `USDJPYc`.
  - Tính toán động: Win Rate, Profit Factor, Net PnL, Max Drawdown.
- [x] **Nginx Reverse Proxy & Watchdog**: Cấu hình reverse proxy tại port `3000/dashboard/` và cron script tự phục hồi dịch vụ mỗi 5 phút.
- [x] **Trade Reporting Engine (`reporting/reporter.py`)**: Báo cáo tài chính email tự động qua MT5 Python API (Daily / Monthly).

---

## 3. Việc Đang Làm Dở Dang & Cần Làm Tiếp (Next Action Items)
*Agent tiếp theo hãy nhận diện và làm tiếp theo thứ tự ưu tiên:*
1. **[Trading Monitor / Optimization]**: Giám sát phản ứng vào lệnh trong phiên Âu (14:00 - 18:00 VN) và phiên Mỹ (19:00 - 23:00 VN) khi thanh khoản tăng cao và bộ lọc D1 EMA50 mở điều kiện.
2. **[Reporting Engine]**: Cấu hình thông tin SMTP (Gmail App Password) vào file [`reporting/config.json`](reporting/config.json) nếu người dùng muốn kích hoạt email báo cáo tự động cuối ngày.
3. **[Performance Analytics]**: Khi cơ sở dữ liệu tích lũy trên 20 lệnh đóng, đánh giá lại tỷ lệ Win Rate và Profit Factor để tinh chỉnh các tham số `TrendRangeBars`, `RiskPerTradePct` trong file set nếu cần.
4. **[Multi-Agent Handover]**: Duy trì cập nhật file này sau mỗi phiên làm việc để đảm bảo các Agent khác (Claude, Codex, Cursor) nắm bắt đầy đủ tiến độ.

---

## 4. Nhật Ký Thay Đổi Của Các Agent (Changelog)
- **2026-11-03 (Antigravity):** Thiết lập `multi-agent-sync` (AGENT_STATE.md, CLAUDE.md, .agent/rules/project-context.md, git-push workflow).
- **2026-11-03 (Antigravity):** Triển khai SQLite Analytics Engine (`analytics_db.py`) và tích hợp KPI cards / Spreads lên Web Dashboard di động.
- **2026-11-02 (Antigravity):** Xây dựng Mobile Web Monitor (`dashboard/service.py`), fix múi giờ Việt Nam GMT+7 và proxy Nginx port 3000.
- **2026-11-02 (Antigravity):** Cấu hình MT5 VPS Docker, kích hoạt đồng thời 2 biểu đồ `XAUUSDc, M5` và `USDJPYc, M5` cho Nyao Scalper v54.0.
