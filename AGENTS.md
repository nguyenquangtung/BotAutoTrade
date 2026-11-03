# AI Agent Operating Guidelines & Rules

## 1. Strict Security Rules (Quy Tắc Bảo Mật Bắt Buộc)

> [!CAUTION]
> 1. **Tuyệt đối không tự ý SSH / remote vào server hoặc môi trường production** (VPS, cloud, MT5 terminal hosting live accounts) để sửa file, đổi cấu hình, deploy hoặc dừng/khởi động bất kỳ tiến trình nào nếu chưa có chỉ thị rõ ràng từ người dùng.
> 2. **Luôn hỏi xác nhận**: Mọi thao tác tác động đến máy chủ từ xa, tài khoản giao dịch live hoặc VPS trading đều bắt buộc dừng lại, giải thích rõ câu lệnh, mục đích, rủi ro và chờ người dùng xác nhận.

## 2. Multi-Agent 5-Step Workflow (Quy Trình 5 Bước Bắt Buộc)

Mỗi khi Agent (Claude, Codex, Antigravity, Cursor) hoàn thành hoặc sửa đổi mã nguồn, bắt buộc tuân thủ đúng 5 bước:
1. **Cập nhật AGENT_STATE.md**: Bắt buộc đọc file này trước khi làm và cập nhật snapshot, task hoàn thành, next actions trước khi kết thúc phiên.
2. **Commit & Push**: Sử dụng `scripts/git-push.ps1` hoặc commit theo chuẩn Conventional Commits.
3. **Graphify Update**: Chạy `graphify . --update --code-only` để đồng bộ lại sơ đồ tri thức codebase.
4. **Cập nhật ARCHITECTURE.md**: Bổ sung thay đổi kiến trúc/flow kỹ thuật vào file [ARCHITECTURE.md](ARCHITECTURE.md) nếu có điều chỉnh luồng dữ liệu, tham số hoặc cơ chế tính toán.
5. **Đồng bộ Dependencies**: Kiểm tra và cập nhật `requirements.txt` nếu có thư viện mới phát sinh.
