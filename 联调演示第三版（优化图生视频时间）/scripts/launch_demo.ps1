param([switch]$Lite, [switch]$Cpu, [switch]$Preview, [switch]$NoBrowser)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
& "$PSScriptRoot\prepare_runtime.ps1"
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
        $modelDir = if ($env:PBSC_LLM_MODEL_DIR) { $env:PBSC_LLM_MODEL_DIR } else { Join-Path (Get-Location) 'data\models\qwen3' }
        $modelName = if ($Lite) { 'Qwen3-0.6B-Q8_0.gguf' } else { 'Qwen3-4B-Instruct-2507-Q4_K_M.gguf' }
        $modelFile = Get-Item -LiteralPath (Join-Path $modelDir $modelName) -ErrorAction SilentlyContinue
        $firstPart = Get-Item -LiteralPath (Join-Path $modelDir ($modelName + '.001')) -ErrorAction SilentlyContinue
        if ((-not $modelFile -or $modelFile.Length -lt 1024) -and ($Lite -or -not $firstPart -or $firstPart.Length -lt 1024)) {
            $modelArgs = if ($Lite) { @('--lite') } else { @() }
            & $Python -B -X utf8 scripts/download_llm_models.py --model-dir $modelDir @modelArgs
            if ($LASTEXITCODE) { throw "Model download failed. Reopen this launcher to retry." }
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
    $browserPath = if ($Preview) { '/?preview=1' } else { '/' }
    $serverArgs = @()
    if (-not $NoBrowser) { $serverArgs = @('--open-browser', $browserPath) }
    & $Python -B -X utf8 -m server @serverArgs
    if ($LASTEXITCODE) { throw "Web server failed." }
} finally {
    if ($llm -and -not $llm.HasExited) { Stop-Process -InputObject $llm }
}
