# Multi-Agent State & Handover Log (AGENT_STATE.md)

## 1. Trạng Thái Hiện Tại (Current Snapshot)
- **Thời điểm cập nhật:** 2026-11-03 09:10 (GMT+7, Vietnam)
- **Agent cập nhật gần nhất:** Claude Code (Opus 5.5)
- **Mục tiêu sản phẩm / Task chính:** Hệ thống giao dịch tự động MetaTrader 5 (Nyao Scalper v54.0) chạy 2 cặp `XAUUSDc` và `USDJPYc` trên tài khoản Cent Exness (`#<ACCOUNT_LOGIN>`), tích hợp Web Monitor di động, Engine phân tích PnL/Tick SQLite và Watchdog tự phục hồi trên Google Cloud VPS Singapore.
- **Trạng thái Repo / Branch:** Branch `main`, Commit hash `32e8582`.
- **Trạng thái VPS Production:**
  - Host VPS: Google Cloud `<VPS_IP>` (`asia-southeast1-c`), User `<VPS_USER>`.
  - Container: `mt5_trading` (WineHQ Staging, MetaTrader 5 Build 4452+).
  - Web Monitor: `http://<VPS_IP>:3000/dashboard/` (Basic Auth: thông tin đăng nhập lưu ngoài repo, xem `.env` trên VPS).
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
0. **[Deploy 2026-11-03 10:12 VN]**
   - Dashboard ĐÃ deploy: `service.py` mới chạy trên `127.0.0.1:8001` (port 8001 không còn truy cập từ Internet), DB đã migrate (fee, net_cent). Backup bản cũ: `~/mt5-trading/backups/20261103-100935/`.
   - EA mới ĐÃ upload nhưng CHƯA gắn lên chart: `MQL5/Experts/nyao_scalper_v54_1.ex5` + `nyao_straddle_v1_7.ex5` (compile local build 6296; MetaEditor CLI qua Wine không tạo được ex5). Preset mới đã chép vào `MQL5/Presets/` (backup: `~/mt5-trading/backups/presets-20261103/`). Chart vẫn chạy `nyao_scalper.ex5` cũ.
   - KHÔNG compile đè `nyao_scalper.ex5`: chart sẽ reload với input cũ `MinimumEquity=20` -> code mới hiểu 2.000 USC > equity -> EA đóng lệnh và tự gỡ.
   - Việc cần user làm qua VNC: gỡ EA cũ trên 2 chart, gắn `nyao_scalper_v54_1` + Load preset tương ứng, rồi File > Profiles > Save (profile trên đĩa vẫn là mặc định EURUSD..., crash là mất chart EA).
   - EA USDJPYc hiện KHÔNG thể vào lệnh: lot 0.01 lỗ 126.65 USC tại stop = 6.73% equity > MaxRiskPerTradePct 5% (cần ~2.533 USC).
   - **Quyết định user (2026-11-03): Phương án A - TẠM TẮT USDJPYc, chỉ chạy XAUUSDc.** Gỡ EA khỏi chart USDJPYc (không gắn v54_1). Chỉ bật lại (gắn `nyao_scalper_v54_1` + `usdjpy-default.set`) khi equity >= ~2.600 USC và user đồng ý. Lý do không để EA tự chặn: risk sizing sẽ tự mở lại ở ~2.533 USC với rủi ro ~5%/lệnh.
1. **[Trading Monitor / Optimization]**: Giám sát phản ứng vào lệnh trong phiên Âu (14:00 - 18:00 VN) và phiên Mỹ (19:00 - 23:00 VN) khi thanh khoản tăng cao và bộ lọc D1 EMA50 mở điều kiện.
2. **[Reporting Engine]**: Cấu hình thông tin SMTP (Gmail App Password) vào file [`reporting/config.json`](reporting/config.json) nếu người dùng muốn kích hoạt email báo cáo tự động cuối ngày.
3. **[Performance Analytics]**: Khi cơ sở dữ liệu tích lũy ít nhất ~100 lệnh đóng (20 lệnh chưa đủ ý nghĩa thống kê, dễ over-fit), so sánh với backtest rồi mới cân nhắc tinh chỉnh tỷ lệ Win Rate và Profit Factor để tinh chỉnh các tham số `TrendRangeBars`, `RiskPerTradePct` trong file set nếu cần.
4. **[Multi-Agent Handover]**: Duy trì cập nhật file này sau mỗi phiên làm việc để đảm bảo các Agent khác (Claude, Codex, Cursor) nắm bắt đầy đủ tiến độ.

---

## 4. Nhật Ký Thay Đổi Của Các Agent (Changelog)
- **2026-11-03 (Claude Code):** Thiết kế lại `dashboard/index.html` theo skill ui-ux-pro-max + frontend-design: dải trạng thái + độ mới dữ liệu (cảnh báo khi cũ/mất kết nối, nút Thử lại), equity lớn + đường equity (từ /api/analytics), thanh khoảng cách tới SL + lỗ tối đa ước tính, lịch sử ghi đúng chiều vị thế, cho phép zoom, chữ >= 13px, số tabular, tương phản >= 4.5:1. ĐÃ deploy 2026-11-03 15:16 (backup `~/mt5-trading/backups/20261103-151342/`).
- **2026-11-03 15:16 (Claude Code):** Upload `nyao_scalper_v54_1.ex5` có fix P&L đóng tay (commit 2b1024b). MT5 trên Wine KHÔNG tự reload khi thay ex5 -> bot vẫn chạy bản cũ trong bộ nhớ cho tới khi user mở F7 > OK trên chart XAUUSDc (hoặc MT5 khởi động lại).
- **2026-11-03 (Claude Code):** Review toàn bộ source + fix: xóa IP/mật khẩu/số tài khoản khỏi tài liệu (giá trị thật trong `.env.vps` local, gitignored); EA: đơn vị Cent, giới hạn lỗ ngày, DrawdownScope=Own, freeze level, DONE_PARTIAL, Discord qua OnTimer, cache ReadTrend/floating PL, sửa macro LogPrint, straddle lưu trạng thái pause; Dashboard: bind 127.0.0.1, Net PnL gồm commission/swap/fee (`dashboard/metrics.py`), spread theo point thật, sync deal tăng dần, 1 kết nối SQLite, log xoay vòng, DD theo equity, HTML tách ra `dashboard/index.html`; `git-push.ps1` không còn `git add .` và chặn commit secret.
- **2026-11-03 (Antigravity):** Thiết lập `multi-agent-sync` (AGENT_STATE.md, CLAUDE.md, .agent/rules/project-context.md, git-push workflow).
- **2026-11-03 (Antigravity):** Triển khai SQLite Analytics Engine (`analytics_db.py`) và tích hợp KPI cards / Spreads lên Web Dashboard di động.
- **2026-11-02 (Antigravity):** Xây dựng Mobile Web Monitor (`dashboard/service.py`), fix múi giờ Việt Nam GMT+7 và proxy Nginx port 3000.
- **2026-11-02 (Antigravity):** Cấu hình MT5 VPS Docker, kích hoạt đồng thời 2 biểu đồ `XAUUSDc, M5` và `USDJPYc, M5` cho Nyao Scalper v54.0.

