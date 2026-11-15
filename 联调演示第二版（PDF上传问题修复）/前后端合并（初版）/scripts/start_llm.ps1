# 启动本地 Qwen3 推理服务（llama.cpp），提供 OpenAI 兼容接口。
# 用法（项目根目录）：
#   powershell -ExecutionPolicy Bypass -File scripts\start_llm.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\start_llm.ps1 -Port 8080 -Ctx 8192
#   powershell -ExecutionPolicy Bypass -File scripts\start_llm.ps1 -Cpu        # 没有可用显卡时
# 启动后地址：http://127.0.0.1:8080/v1/chat/completions

param(
    [int]$Port = 8080,
    [int]$Ctx = 8192,
    [int]$GpuLayers = 99,
    [int]$Threads = 8,
    [string]$Model = "",
    [string]$Device = "",          # 例如 Vulkan1；留空则自动找 NVIDIA 设备
    [switch]$Background,
    [switch]$Lite,
    [switch]$Cpu
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$Root = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root "third_party\llama.cpp"
$Exe = Get-ChildItem -Path $Runtime -Recurse -Filter "llama-server.exe" -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $Exe) {
    Write-Host "找不到 third_party\llama.cpp\llama-server.exe" -ForegroundColor Red
    Write-Host "请解压 llama.cpp 的 Windows 预编译包到 third_party\llama.cpp\（见 third_party\llama.cpp\SOURCE.md）"
    exit 1
}

if ($Lite) { $Cpu = $true }
if (-not $Model) {
    $name = if ($Lite) { "Qwen3-0.6B-Q8_0.gguf" } else { "Qwen3-4B-Instruct-2507-Q4_K_M.gguf" }
    $modelDir = if ($env:PBSC_LLM_MODEL_DIR) { $env:PBSC_LLM_MODEL_DIR } else { Join-Path $Root "data\models\qwen3" }
    $Model = Join-Path $modelDir $name
}
if (-not (Test-Path $Model)) {
    Write-Host "找不到模型文件：$Model" -ForegroundColor Red
    Write-Host "请先运行：.venv\Scripts\python scripts\download_llm_models.py"
    exit 1
}

if ($Cpu) { $GpuLayers = 0 }

# 双显卡笔记本的 Vulkan 设备顺序不固定（常见：Vulkan0=核显，Vulkan1=独显），
# 不指定时 llama.cpp 可能选到核显。这里自动挑第一块 NVIDIA 显卡。
if (-not $Cpu -and -not $Device) {
    $devices = & $Exe.FullName --list-devices 2>$null
    foreach ($line in $devices) {
        if ($line -match '(Vulkan\d+):.*(NVIDIA|GeForce|RTX|Quadro)') {
            $Device = $Matches[1]
            break
        }
    }
    if (-not $Device) { $GpuLayers = 0 }
}

# 运行时 DLL 与可执行文件放在同一目录；从该目录启动，避免找不到 DLL
$WorkDir = Split-Path -Parent $Exe.FullName
Write-Host "llama-server: $($Exe.FullName)"
Write-Host "model       : $Model"
Write-Host "device      : $(if ($Device) { $Device } else { 'auto/CPU' })"
Write-Host "endpoint    : http://127.0.0.1:$Port/v1/chat/completions (ctx=$Ctx, ngl=$GpuLayers)"
Write-Host "按 Ctrl+C 停止"
Write-Host ""

$serverArgs = @(
    "--model", $Model,
    "--host", "127.0.0.1",
    "--port", "$Port",
    "--ctx-size", "$Ctx",
    "--n-gpu-layers", "$GpuLayers",
    "--threads", "$Threads",
    "--parallel", "1",
    "--flash-attn", "on",
    "--ubatch-size", "1024",
    "--alias", "qwen3-4b-instruct-2507",
    "--reasoning-budget", "0"
)
if ($Device -and -not $Cpu) { $serverArgs += @("--device", $Device) }
if ($Background) {
    $logDir = Join-Path $Root "logs"
    New-Item -ItemType Directory -Force $logDir | Out-Null
    $commandLine = ($serverArgs | ForEach-Object { '"' + $_ + '"' }) -join ' '
    return Start-Process -FilePath $Exe.FullName -ArgumentList $commandLine -WorkingDirectory $WorkDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir "llama-stdout.log") -RedirectStandardError (Join-Path $logDir "llama-stderr.log")
}
Push-Location $WorkDir
try {
    & $Exe.FullName @serverArgs
} finally {
    Pop-Location
}
