param([switch]$Lite, [switch]$Cpu, [switch]$Preview)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$Python = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Run the setup batch file first." }
$llm = $null
try {
    if (-not $Preview) {
        if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) {
            throw "Port 8080 is already in use. Stop the old model process first."
        }
        $llm = & "$PSScriptRoot\start_llm.ps1" -Background -Lite:$Lite -Cpu:$Cpu
        $deadline = (Get-Date).AddMinutes(3)
        do {
            if ($llm.HasExited) { throw "Local model exited during startup. Inspect logs/llama-stderr.log." }
            try { $ready = (Invoke-RestMethod http://127.0.0.1:8080/health -TimeoutSec 2).status -eq "ok" }
            catch { $ready = $false }
            if (-not $ready) { Start-Sleep -Milliseconds 800 }
        } until ($ready -or (Get-Date) -ge $deadline)
        if (-not $ready) { throw "Local model did not finish loading." }
        & $Python -X utf8 scripts/warmup_llm.py
        if ($LASTEXITCODE) { throw "Local model warmup failed." }
        $env:SIMPLE_STORY_WRITER_URL = "http://127.0.0.1:8080/v1/chat/completions"
        $env:SIMPLE_STORY_WRITER_MODEL = "qwen3-4b-instruct-2507"
        $env:PBSC_CHILD_REVIEW = "1"
    }
    & $Python -X utf8 scripts/upgrade_reader_data.py
    if ($LASTEXITCODE) { throw "Sample preparation failed." }
    if ($Preview) { Write-Host "Open /?preview=1 on the displayed server address." }
    & $Python -X utf8 -m server
    if ($LASTEXITCODE) { throw "Web server failed." }
} finally {
    if ($llm -and -not $llm.HasExited) { Stop-Process -InputObject $llm }
}
