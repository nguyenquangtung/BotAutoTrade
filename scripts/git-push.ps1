param(
    [Parameter(Mandatory=$true)]
    [string]$Message,
    [string]$Branch = "main",
    # Also stage untracked files (off by default so stray files/secrets aren't committed by accident)
    [switch]$IncludeUntracked
)

$repoRoot = git rev-parse --show-toplevel
Set-Location $repoRoot

# 1. Kiểm tra trạng thái Git
$status = git status --porcelain
if (-not $status) {
    Write-Host "Không có thay đổi nào cần commit." -ForegroundColor Yellow
    exit 0
}

# 2. Stage: chỉ file đã được track, trừ khi truyền -IncludeUntracked
if ($IncludeUntracked) {
    git add -A
} else {
    git add -u
    $untracked = git ls-files --others --exclude-standard
    if ($untracked) {
        Write-Host "File mới CHƯA được add (dùng -IncludeUntracked hoặc git add thủ công):" -ForegroundColor Yellow
        $untracked | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
    }
}

# 3. Quét secret trong phần đã stage: các giá trị thật lấy từ .env.vps (file local, gitignored)
$secretFile = Join-Path $repoRoot ".env.vps"
if (Test-Path $secretFile) {
    $secrets = Get-Content $secretFile |
        Where-Object { $_ -match '^\s*[A-Z_]+\s*=\s*(.+)$' } |
        ForEach-Object { ($_ -split '=', 2)[1].Trim() } |
        Where-Object { $_.Length -ge 6 -and $_ -notmatch '^(Exness|asia-)' }
    $staged = git diff --cached -U0 | Out-String
    $leaks = $secrets | Where-Object { $staged.Contains($_) }
    if ($leaks) {
        Write-Error "Phát hiện thông tin bảo mật (từ .env.vps) trong thay đổi đã stage. Đã hủy commit."
        git reset -q
        exit 1
    }
}

git commit -m "$Message"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 4. Push lên remote
Write-Host "Đang đẩy code lên GitHub ($Branch)..." -ForegroundColor Cyan
git push origin $Branch

if ($LASTEXITCODE -eq 0) {
    Write-Host "Push GitHub thành công!" -ForegroundColor Green

    # 5. Tự động chạy graphify update nếu có graphify
    if (Get-Command graphify -ErrorAction SilentlyContinue) {
        Write-Host "Đang cập nhật knowledge graph..." -ForegroundColor Cyan
        graphify . --update --code-only
        Write-Host "Cập nhật Graphify hoàn tất." -ForegroundColor Green
    }

    # 6. Nhắc nhở cập nhật AGENT_STATE.md & ARCHITECTURE.md
    Write-Host "[State Check] Đảm bảo AGENT_STATE.md đã được cập nhật trước khi bàn giao cho Agent khác." -ForegroundColor Magenta
    Write-Host "[Doc Check] Cập nhật ARCHITECTURE.md nếu commit này thay đổi luồng kỹ thuật." -ForegroundColor Yellow
    Write-Host "[Requirements Check] Kiểm tra và đồng bộ requirements.txt nếu có dependencies mới." -ForegroundColor Yellow
} else {
    Write-Error "Lỗi khi push lên GitHub. Vui lòng kiểm tra lại quyền truy cập hoặc conflict."
}
