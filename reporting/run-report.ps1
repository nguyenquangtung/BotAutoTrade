param (
    [string]$Type = "daily",
    [switch]$PreviewOnly = $false
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

if ($PreviewOnly) {
    Write-Host "[INFO] Generating HTML preview without sending email..." -ForegroundColor Cyan
    python reporter.py --type $Type --no-mail --save-html report_preview.html
    if (Test-Path report_preview.html) {
        Write-Host "[SUCCESS] Preview generated: $ScriptDir\report_preview.html" -ForegroundColor Green
        Start-Process "report_preview.html"
    }
} else {
    Write-Host "[INFO] Running report ($Type) and sending email via config.json..." -ForegroundColor Cyan
    python reporter.py --type $Type --config config.json --save-html report_preview.html
}

