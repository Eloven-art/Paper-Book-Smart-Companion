param([string]$Output = (Join-Path $PSScriptRoot "audio"))
$ErrorActionPreference = "Stop"
$voice = New-Object -ComObject SAPI.SpVoice
$chinese = $voice.GetVoices("Language=804")
if ($chinese.Count -eq 0) { throw "Install a Mandarin SAPI voice, or record consenting adults as q01.wav to q30.wav." }
$voice.Voice = $chinese.Item(0)
New-Item -ItemType Directory -Force $Output | Out-Null
$cases = Get-Content (Join-Path $PSScriptRoot "demo_questions.json") -Encoding UTF8 -Raw | ConvertFrom-Json
foreach ($item in $cases) {
    $stream = New-Object -ComObject SAPI.SpFileStream
    $stream.Open((Join-Path $Output ($item.id + ".wav")),3,$false)
    try {
        $voice.AudioOutputStream = $stream
        [void] $voice.Speak($item.question)
    } finally { $stream.Close() }
}
