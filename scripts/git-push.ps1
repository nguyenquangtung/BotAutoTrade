param(
    [Parameter(Mandatory=$true)]
    [string]$Message,
    [string]$Branch = "main"
)

# 1. Commit & Push
git add .
git commit -m "$Message"
git push origin $Branch

if ($LASTEXITCODE -ne 0) {
    Write-Error "Push thất bại. Dừng quy trình."
    exit 1
}

# 2. Update Graphify Knowledge Graph
Write-Host "[Graphify] Đang cập nhật knowledge graph..." -ForegroundColor Cyan
graphify . --update

# 3. Yêu cầu AI cập nhật ARCHITECTURE.md
Write-Host "[Doc] Sẵn sàng cập nhật ARCHITECTURE.md phản ánh luồng kỹ thuật mới." -ForegroundColor Green

# 4. Kiểm tra dependencies
Write-Host "[Requirements] Rà soát và cập nhật requirements.txt/package.json nếu có thư viện mới." -ForegroundColor Yellow

