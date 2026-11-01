# Nyao Scalper MT5 - Technical Architecture & Handover Documentation

## 1. Tổng Quan Kiến Trúc (Architecture Overview)

Dự án là một hệ thống Auto Scalping định lượng xây dựng cho nền tảng **MetaTrader 5 (MT5)**, kết hợp giữa mã giao dịch MQL5 thực thi trực tiếp trên terminal và công cụ tự động hóa backtesting bằng Python.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        MetaTrader 5 Terminal                           │
│                                                                        │
│  ┌───────────────────────┐             ┌────────────────────────────┐  │
│  │   nyao_scalper.mq5    │             │     nyao_straddle.mq5      │  │
│  │   (Trend Scalper EA)  │             │   (Extreme Volatility EA)  │  │
│  └──────────┬────────────┘             └─────────────┬──────────────┘  │
│             │                                        │                 │
│             ▼                                        ▼                 │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                     Order Execution & Risk Core                  │  │
│  │      - Dynamic Lot Sizing (RiskPerTradePct)                      │  │
│  │      - Multi-level Checkpoint Lock & Trailing                    │  │
│  │      - Basket Stop & Total Loss Protection                       │  │
│  └──────────────────────────────────┬───────────────────────────────┘  │
│                                     │                                  │
│                                     ▼                                  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                  Binary State Persistence                        │  │
│  │        (MQL5\Files\NyaoScalper\ state recovery on crash)         │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────┬──────────────────────────────────┘
                                      │
                                      ▼
┌────────────────────────────────────────────────────────────────────────┐
│                  Off-Terminal Backtest & Reporting                     │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │     tools/backtest/nyao_backtest.py                              │  │
│  │     - MT5 Terminal lifecycle & CPU affinity control             │  │
│  │     - Batch monthly real-tick test executor                      │  │
│  │     - Report generator (summary.md, index.html, CSV, SVG graphs) │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Các Thành Phần Chính & Luồng Kỹ Thuật (Technical Flows)

### 2.1. Nyao Scalper (`nyao_scalper.mq5`) - Core Trend Scalping EA
- **Cơ chế tính Signal Score (0.0 đến 10.0)**:
  - Tính toán dựa trên 4 thành phần đa khung thời gian:
    1. **Range Position** (`TrendRangeBars = 200`): Vị trí giá đóng cửa trong biên độ dao động.
    2. **EMA Distance** (`TrendEMAPeriod = 200`): Khoảng cách tương đối đến đường trung bình động tính theo ATR.
    3. **Efficiency Ratio** (`TrendERBars = 100`): Tỷ số giữa bước dịch chuyển ròng so với tổng đường đi thực tế.
    4. **Momentum / ROC** (`TrendROCBars = 192`): Tốc độ biến động ròng đo bằng đơn vị ATR.
  - Tổng điểm phân định chiều: `Buy Score = 5 + 5s`, `Sell Score = 5 - 5s`.
- **Cơ chế lọc & kích hoạt lệnh**:
  - `EnableNewBarEntryOnly`: Chỉ đánh giá vào lệnh khi nến đóng để loại trừ repaint.
  - `EnableHTFTrendFilter`: Bộ lọc xu hướng nến D1 (EMA50) – chỉ Buy khi giá trên EMA đang dốc lên, Sell khi giá dưới EMA dốc xuống.
  - Phân loại vào lệnh: Market Order hoặc Pullback Limit (`EnableLimitEntry` neo theo ATR / EMA / Swing).
- **Quản lý rủi ro & Checkpoint Exit**:
  - Không dùng TP cố định mặc định. Sử dụng **Checkpoint Exit**: khi giá đạt mốc TP mục tiêu (ví dụ mỗi mốc $20 đối với Vàng), dời SL khóa lợi nhuận (`CheckpointLockPct = 50%`) và kéo trail theo sau.
  - **Trend-flip Exit**: Nếu đảo chiều xu hướng rõ rệt (điểm ngược chiều $\ge 6.0$), lệnh lập tức được tất toán sớm trước khi chạm full SL.
  - **Risk-based Lot Sizing**: Lot được tính toán tự động dựa trên mức chịu lỗ tối đa của số dư tài khoản (`RiskPerTradePct`, mặc định 1.25%).
  - **Hedge Chain (Martingale Phục Hồi)**: Tính năng mở rộng (mặc định tắt) kích hoạt khi vị thế âm vượt ngưỡng `HedgeTriggerATR` và xu hướng đối nghịch xác nhận.

### 2.2. Nyao Straddle (`nyao_straddle.mq5`) - Companion EA
- Chuyên biệt cho **XAUUSD khung M1** trong các phiên biến động cực đại (News / Market Crash).
- **Điều kiện kích hoạt**:
  1. H1 ATR(14) $\ge 34\$$ (thị trường cực đoan).
  2. M1 ATR(7) $\ge 2\times$ mức trung bình 288 nến.
- **Phương thức thực thi**: Đặt cặp lệnh chờ đối xứng `Buy Stop` ($+1\$) và `Sell Stop` ($-1\$). Lệnh nào khớp sẽ hủy lệnh còn lại và kích hoạt trailing stop $1\$ bám sát ngay lập tức.

### 2.3. Automated Backtest Harness (`tools/backtest/nyao_backtest.py`)
- Quản lý quy trình kiểm thử tự động hàng loạt qua MT5 terminal:
  - Tự động biên dịch file `.mq5` bằng `metaeditor64.exe`.
  - Điều khiển nạp cấu hình `.set`, khởi tạo MT5 ở chế độ nền với mức CPU affinity được giới hạn để tối ưu tài nguyên.
  - Thu thập dữ liệu log vốn (Equity/Balance log từng 5 phút) và trích xuất báo cáo HTML/CSV/Markdown.

---

## 3. Cấu Trúc Thư Mục

- `nyao_scalper.mq5`: Source code EA chính (Scalping đa chỉ báo).
- `nyao_straddle.mq5`: Source code EA phụ (Straddle biến động cực hạn).
- `settings/`: Các profile thông số tinh chỉnh sẵn:
  - `xauusd-default.set`: Thiết lập chuẩn cho Vàng (XAUUSD M5, rủi ro 1.25%).
  - `xauusd-high-risk.set`: Thiết lập rủi ro cao (XAUUSD M5, rủi ro 2.5%).
  - `usdjpy-default.set`: Thiết lập giao dịch cặp USDJPY M5 (chạy song song cân bằng danh mục).
  - `xauusd-straddle.set`: Thiết lập cho Nyao Straddle M1.
- `tools/backtest/`: Script tự động hóa kiểm thử chiến lược.
- `reports/`: Dữ liệu và biểu đồ kết quả backtest chi tiết giai đoạn 2022 - 2026.
- `scripts/`: Chứa script `git-push.ps1` hỗ trợ tự động hóa đẩy code và cập nhật graphify.

---

## 4. Hướng Dẫn Vận Hành & Yêu Cầu Cài Đặt (Operations & Handover)

1. **Yêu cầu môi trường**:
   - Nền tảng: Windows OS.
   - Phần mềm: **MetaTrader 5 (64-bit)** kèm tài khoản giao dịch **Hedging** (Exness, IC Markets, Tickmill, FTMO, v.v.).
   - Python: Python 3.8+ (đã có sẵn 3.11 trong môi trường).
2. **Cài đặt EA vào MT5**:
   - Mở MT5 -> Vào menu `File` -> `Open Data Folder` -> chuyển tới `MQL5\Experts`.
   - Copy file `nyao_scalper.mq5` (và `nyao_straddle.mq5` nếu dùng) vào thư mục `Experts`.
   - Bấm `F4` mở MetaEditor, chọn file và bấm `Compile` (F7) để tạo file `.ex5`.
   - Kéo EA vào chart **XAUUSD khung M5**, nhấn **Load** file cấu hình tương ứng trong thư mục `settings/` (ví dụ `settings/xauusd-default.set`).
   - Bật nút **Algo Trading** trên thanh công cụ của MT5.
3. **Lưu ý cấu hình tài khoản**:
   - Tài khoản Standard khuyến nghị vốn $\ge 2,000\$$ (để lot 0.01 chịu rủi ro $\le 1.25\%$). Nếu vốn nhỏ từ $400\$ đến $1,600\$$, hệ thống cho phép rủi ro tối đa 5% cho lot 0.01 tối thiểu.
   - Với tài khoản Cent: Vốn tối thiểu từ $100\$$ (tương đương 10,000 cents).

---

## 5. Kiến Trúc Triển Khai Server 24/7 (Google Cloud Singapore VPS)

Hệ thống chạy 24/7 độc lập trên Cloud VPS tốc độ cao với độ trễ siêu thấp đến sàn Exness:
- **Cloud Provider**: Google Cloud Platform (Compute Engine).
- **Cấu hình máy chủ**: `e2-standard-2` (2 vCPU AMD EPYC 7B12, 8 GB RAM, 30 GB SSD, Ubuntu 22.04 LTS).
- **Vùng (Region/Zone)**: `asia-southeast1-c` (Singapore) — **Latency tới Exness chỉ ~1.5 ms**, tối ưu tối đa cho scalping vàng (XAUUSD M5).
- **Công nghệ Container**: Docker + WineHQ Staging 11.10 (hỗ trợ tập lệnh AVX2, vượt qua Themida Anti-Cheat / Debugger check) + KasmVNC Web GUI + Nginx Reverse Proxy.
- **Image**: `mt5_trading:custom` (tích hợp sẵn Wine Staging 11.10, xdotool, symlinks).
- **Thư mục triển khai**: `/home/<VPS_USER>/mt5-trading/` (Persistent storage tại `./config` bảo toàn thông tin tài khoản, preset và lịch sử giao dịch qua các lần reboot).
- **Cổng dịch vụ & Truy cập**:
  - `3000`: Web VNC GUI truy cập trực tiếp qua trình duyệt: `http://<VPS_IP>:3000` (User: `admin`, Pass: `<REDACTED>`).
  - `8001`: Python/RPyC IPC socket (`mt5linux`) phục vụ report/bot notification service.
- **Tự động hóa**: Cấu hình tự động khởi động MT5 Terminal cùng bot khi máy chủ khởi động lại (`restart: unless-stopped`).


