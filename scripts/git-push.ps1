param(
    [Parameter(Mandatory=$true)]
    [string]$Message,
    [string]$Branch = "main"
)

# 1. Kiểm tra trạng thái Git
$status = git status --porcelain
if (-not $status) {
    Write-Host "Không có thay đổi nào cần commit." -ForegroundColor Yellow
    exit 0
}

# 2. Add và Commit
git add .
git commit -m "$Message"

# 3. Push lên remote
Write-Host "Đang đẩy code lên GitHub ($Branch)..." -ForegroundColor Cyan
git push origin $Branch

if ($LASTEXITCODE -eq 0) {
    Write-Host "Push GitHub thành công!" -ForegroundColor Green
    
    # 4. Tự động chạy graphify update nếu có graphify
    if (Get-Command graphify -ErrorAction SilentlyContinue) {
        Write-Host "Đang cập nhật knowledge graph..." -ForegroundColor Cyan
        graphify . --update --code-only
        Write-Host "Cập nhật Graphify hoàn tất." -ForegroundColor Green
    }

    # 5. Nhắc nhở cập nhật AGENT_STATE.md & ARCHITECTURE.md
    Write-Host "[State Check] Đảm bảo AGENT_STATE.md đã được cập nhật trước khi bàn giao cho Agent khác." -ForegroundColor Magenta
    Write-Host "[Doc Check] Cập nhật ARCHITECTURE.md nếu commit này thay đổi luồng kỹ thuật." -ForegroundColor Yellow
    Write-Host "[Requirements Check] Kiểm tra và đồng bộ requirements.txt nếu có dependencies mới." -ForegroundColor Yellow
} else {
    Write-Error "Lỗi khi push lên GitHub. Vui lòng kiểm tra lại quyền truy cập hoặc conflict."
}
