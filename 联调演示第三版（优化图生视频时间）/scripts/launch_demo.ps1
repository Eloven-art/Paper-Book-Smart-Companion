param([switch]$Lite, [switch]$Cpu, [switch]$Preview)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$Python = Join-Path (Get-Location) "runtime\python\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = Join-Path (Get-Location) ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python)) { throw "Extract the complete package or run the setup batch file." }
if ((Get-Item -LiteralPath $Python).Length -lt 1024) {
    throw "The bundled Python is a Git LFS pointer. Run git lfs install and git lfs pull."
}
$env:PYTHONDONTWRITEBYTECODE = "1"
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
        $warmupArgs = @()
        if ($Cpu -or $Lite) { $warmupArgs = @("--repeats", "4", "--rounds", "1") }
        & $Python -B -X utf8 scripts/warmup_llm.py @warmupArgs
        if ($LASTEXITCODE) { throw "Local model warmup failed." }
        $env:SIMPLE_STORY_WRITER_URL = "http://127.0.0.1:8080/v1/chat/completions"
        $env:SIMPLE_STORY_WRITER_MODEL = "qwen3-4b-instruct-2507"
        # 校赛使用经过审核的生成内容。
        $env:PBSC_CHILD_REVIEW = "1"
    }
    & $Python -B -X utf8 scripts/upgrade_reader_data.py
    if ($LASTEXITCODE) { throw "Sample preparation failed." }
    if ($Preview) { Write-Host "Open /?preview=1 on the displayed server address." }
    & $Python -B -X utf8 -m server
    if ($LASTEXITCODE) { throw "Web server failed." }
} finally {
    if ($llm -and -not $llm.HasExited) { Stop-Process -InputObject $llm }
}
