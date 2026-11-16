$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime-assets.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$lfsApi = 'https://github.com/Eloven-art/Paper-Book-Smart-Companion.git/info/lfs/objects/batch'
$downloads = Join-Path $root '.runtime-downloads'

foreach ($asset in $manifest.assets) {
    $missing = @($asset.files | Where-Object {
        $item = Get-Item -LiteralPath (Join-Path $root $_.path) -ErrorAction SilentlyContinue
        -not $item -or $item.Length -ne $_.bytes
    })
    if (-not $missing) { continue }

    $packed = Join-Path (Join-Path $root 'runtime/packs') $asset.name
    $packedFile = Get-Item -LiteralPath $packed -ErrorAction SilentlyContinue
    $bundled = $packedFile -and $packedFile.Length -eq $asset.bytes
    $archivePath = $packed
    if (-not $bundled) {
        New-Item -ItemType Directory -Force -Path $downloads | Out-Null
        $archivePath = Join-Path $downloads ($asset.name + '.part')
    }
    if ((Test-Path -LiteralPath $archivePath) -and (Get-Item -LiteralPath $archivePath).Length -gt $asset.bytes) {
        Remove-Item -LiteralPath $archivePath
    }
    if (-not (Test-Path -LiteralPath $archivePath) -or (Get-Item -LiteralPath $archivePath).Length -ne $asset.bytes) {
        Write-Host ('Downloading {0} ({1:N0} MB)' -f $asset.name, ($asset.bytes / 1MB))
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $request = @{ operation = 'download'; transfers = @('basic'); objects = @(
            @{ oid = $asset.sha256; size = $asset.bytes }
        ) } | ConvertTo-Json -Depth 4 -Compress
        $type = 'application/vnd.git-lfs+json'
        $reply = Invoke-RestMethod -Uri $lfsApi -Method Post -ContentType $type -Headers @{ Accept = $type } -Body $request -TimeoutSec 30
        $action = $reply.objects[0].actions.download
        if (-not $action.href -or ([Uri]$action.href).Scheme -ne 'https') {
            throw "GitHub did not provide a download for $($asset.name). Reopen this launcher to retry."
        }
        $curl = Join-Path $env:SystemRoot 'System32\curl.exe'
        $curlArgs = @('--fail', '--location', '--retry', '2', '--connect-timeout', '20', '--output',
                      $archivePath, $action.href)
        foreach ($header in $action.header.PSObject.Properties) {
            $curlArgs += @('--header', ($header.Name + ': ' + $header.Value))
        }
        & $curl --continue-at - @curlArgs
        if ($LASTEXITCODE -eq 33) {
            Remove-Item -LiteralPath $archivePath -ErrorAction SilentlyContinue
            & $curl @curlArgs
        }
        if ($LASTEXITCODE) {
            throw "Download failed: $($asset.name). Reopen this launcher to retry."
        }
    }
    $algorithm = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($archivePath)
    try {
        $hash = [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '')
    } finally {
        $stream.Dispose()
        $algorithm.Dispose()
    }
    if ((Get-Item -LiteralPath $archivePath).Length -ne $asset.bytes -or $hash -ne $asset.sha256) {
        if (-not $bundled) { Remove-Item -LiteralPath $archivePath }
        throw "Checksum failed: $($asset.name). Obtain a fresh copy and retry."
    }

    Write-Host "Installing $($asset.name)..."
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        foreach ($file in $missing) {
            $entry = $archive.GetEntry($file.path)
            if (-not $entry -or $entry.Length -ne $file.bytes) {
                throw "Archive is missing $($file.path)"
            }
            $target = [IO.Path]::GetFullPath((Join-Path $root $file.path))
            if (-not $target.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Invalid project path: $($file.path)"
            }
            New-Item -ItemType Directory -Force -Path ([IO.Path]::GetDirectoryName($target)) | Out-Null
            $temporary = $target + '.pbsc-extract'
            try {
                [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $temporary, $true)
                Move-Item -LiteralPath $temporary -Destination $target -Force
            } finally {
                if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
            }
        }
    } finally {
        $archive.Dispose()
    }
    if (-not $bundled) { Remove-Item -LiteralPath $archivePath }
}
