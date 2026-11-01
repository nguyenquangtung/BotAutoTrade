# AI Agent Operating Guidelines & Rules

## 1. Strict Security Rules (Quy Tắc Bảo Mật Bắt Buộc)

> [!CAUTION]
> 1. **Tuyệt đối không tự ý SSH / remote vào server hoặc môi trường production** (VPS, cloud, MT5 terminal hosting live accounts) để sửa file, đổi cấu hình, deploy hoặc dừng/khởi động bất kỳ tiến trình nào nếu chưa có chỉ thị rõ ràng từ người dùng.
> 2. **Luôn hỏi xác nhận**: Mọi thao tác tác động đến máy chủ từ xa, tài khoản giao dịch live hoặc VPS trading đều bắt buộc dừng lại, giải thích rõ câu lệnh, mục đích, rủi ro và chờ người dùng xác nhận.

## 2. Code & Commit Workflow

Mỗi khi thực hiện thay đổi mã nguồn hoặc đẩy commit, Agent bắt buộc tuân thủ quy trình 4 bước:
1. **Commit & Push**: Thực hiện commit với thông điệp rõ ràng theo chuẩn Conventional Commits.
2. **Graphify Update**: Chạy `graphify . --update` để đồng bộ lại sơ đồ tri thức codebase.
3. **Cập nhật ARCHITECTURE.md**: Bổ sung thay đổi kiến trúc/flow kỹ thuật vào file [ARCHITECTURE.md](ARCHITECTURE.md) nếu có điều chỉnh luồng dữ liệu, tham số hoặc cơ chế tính toán.
4. **Đồng bộ Dependencies**: Kiểm tra và cập nhật `requirements.txt` hoặc tài liệu cài đặt nếu có thư viện mới phát sinh.

