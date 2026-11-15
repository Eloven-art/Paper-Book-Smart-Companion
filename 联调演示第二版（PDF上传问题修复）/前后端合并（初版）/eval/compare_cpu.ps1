param([string]$AudioDir = "", [int]$Limit = 30)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$python = (Resolve-Path ".venv\Scripts\python.exe").Path
foreach ($port in @(8080,8011)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw "Port $port is in use." }
}
$env:PBSC_PORT = "8011"
$env:PBSC_HOST = "127.0.0.1"
$env:SIMPLE_STORY_WRITER_URL = "http://127.0.0.1:8080/v1/chat/completions"
$env:SIMPLE_STORY_WRITER_MODEL = "qwen3-4b-instruct-2507"
$env:PBSC_CHILD_REVIEW = "1"
$env:PBSC_QA_TTS = "0"
$env:PBSC_EMBEDDINGS_URL = " "
$env:PBSC_EMBEDDINGS_MODEL = " "
function Wait-Ready($url, $process) {
    for ($i=0; $i -lt 150; $i++) {
        if ($process.HasExited) { throw "$url process exited." }
        try {
            $reply = Invoke-WebRequest -UseBasicParsing $url -TimeoutSec 2
            if ($reply.StatusCode -eq 200) { return }
        } catch {}
        Start-Sleep -Seconds 1
    }
    throw "Timed out waiting for $url."
}
foreach ($profile in @("lite","standard")) {
    $llm = $null
    $web = $null
    $evalProcess = $null
    [long]$peak = 0
    try {
        $llm = & scripts/start_llm.ps1 -Cpu -Background -Lite:($profile -eq "lite")
        Wait-Ready "http://127.0.0.1:8080/health" $llm
        & $python -X utf8 scripts/warmup_llm.py --repeats 4 --rounds 1
        if ($LASTEXITCODE) { throw "Warmup failed." }
        $web = Start-Process $python -ArgumentList "-X utf8 -m server" -WindowStyle Hidden -PassThru
        Wait-Ready "http://127.0.0.1:8011/api/v1/health" $web
        $report = "data/school-eval/$profile.json"
        $arguments = "-X utf8 eval/run_school_eval.py --url http://127.0.0.1:8011 --label $profile-CPU --out $report --limit $Limit"
        if ($AudioDir) { $arguments += " --audio-dir " + [char]34 + $AudioDir + [char]34 }
        $evalProcess = Start-Process $python -ArgumentList $arguments -WindowStyle Hidden -PassThru
        while (-not $evalProcess.HasExited) {
            $llm.Refresh()
            $peak = [Math]::Max($peak,$llm.WorkingSet64)
            Start-Sleep -Milliseconds 500
        }
        if (-not (Test-Path $report)) { throw "Evaluation did not create $report." }
        $data = Get-Content $report -Encoding UTF8 -Raw | ConvertFrom-Json
        $data | Add-Member NoteProperty peak_llm_working_set_mib ([Math]::Round($peak / 1MB, 1))
        $data | ConvertTo-Json -Depth 20 | Set-Content $report -Encoding UTF8
        if ($evalProcess.ExitCode) { Write-Warning "$profile had request errors. Check its report." }
    } finally {
        foreach ($process in @($evalProcess,$web,$llm)) {
            if ($process -and -not $process.HasExited) { Stop-Process -InputObject $process }
        }
    }
}
