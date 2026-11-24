# The native front end against Pythia's real Backend on Windows (a named pipe), as integration.sh does on Linux and macOS.
# Usage: integration.ps1 -Exe path\to\pythia-ui.exe -Out folder
#   1. no Pythia has run here: --probe says why and exits 2
#   2. the real Backend: --probe gets hello and get_system_info ok
#   3. reconnect: --watch sees ready, the Backend is killed (unavailable), started again (ready again, in time)
#   4. the real window on the runner's desktop against a Backend that emits gamesChanged; a screenshot; exit 0 asserted
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [Parameter(Mandatory = $true)][string]$Out
)
$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force $Out | Out-Null
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$env:PYTHONPATH = $root
$script:server = $null

function Start-Backend([string]$dir, [string]$log, [string]$emit = "") {
    New-Item -ItemType Directory -Force $dir | Out-Null
    $env:PYTHIA_DATA_DIR = $dir
    if ($emit) { $env:PYTHIA_SERVE_EMIT = $emit } else { Remove-Item Env:PYTHIA_SERVE_EMIT -ErrorAction SilentlyContinue }
    $script:server = Start-Process python -ArgumentList (Join-Path $root "ui-native\ci\serve_backend.py") -PassThru -NoNewWindow `
        -RedirectStandardOutput $log -RedirectStandardError "$log.err"
    if (-not (Wait-For $log '^ready' 120)) {
        Get-Content $log, "$log.err" -ErrorAction SilentlyContinue
        throw "the Backend did not come up"
    }
}

function Stop-Backend {
    if ($script:server -and -not $script:server.HasExited) { Stop-Process -Id $script:server.Id -Force }
}

function Wait-For([string]$file, [string]$pattern, [int]$seconds) {
    for ($i = 0; $i -lt $seconds; $i++) {
        if ((Test-Path $file) -and (Select-String -Path $file -Pattern $pattern -Quiet)) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Save-Screenshot([string]$path) {
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
    $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
}

try {
    Write-Host "== 1. no Pythia has run here"
    $env:PYTHIA_DATA_DIR = Join-Path $env:RUNNER_TEMP "pythia-none"
    $none = & $Exe --probe
    $code = $LASTEXITCODE
    $none | Tee-Object -FilePath (Join-Path $Out "probe-none.txt")
    if ($code -ne 2) { throw "probe with no Pythia exited $code, not 2" }
    if (-not (($none -join "`n") -like "*unavailable: Pythia has not run on this computer yet*")) { throw "no reason given" }

    Write-Host "== 2. the real Backend"
    Start-Backend (Join-Path $env:RUNNER_TEMP "pythia-data") (Join-Path $Out "server-probe.log")
    $env:PYTHIA_DATA_DIR = Join-Path $env:RUNNER_TEMP "pythia-data"
    $out = & $Exe --probe
    $code = $LASTEXITCODE
    $out | Tee-Object -FilePath (Join-Path $Out "probe.txt")
    if ($code -ne 0) { throw "probe exited $code" }
    $hello = ($out | Where-Object { $_ -like 'hello: *' }) -join ''
    $info = ($out | Where-Object { $_ -like 'get_system_info: *' }) -join ''
    if (-not ($hello -like '*"status":"ok"*' -and $hello -like '*"server":"pythia"*')) { throw "no ok hello" }
    if (-not ($info -like '*"status":"ok"*' -and $info -like '*"system_uuid"*')) { throw "no ok get_system_info" }
    Stop-Backend

    Write-Host "== 3. reconnect: the Backend is killed and started again while the client runs"
    $dir = Join-Path $env:RUNNER_TEMP "pythia-reconnect"
    Start-Backend $dir (Join-Path $Out "server-reconnect-1.log")
    $env:PYTHIA_DATA_DIR = $dir
    $watchLog = Join-Path $Out "watch.log"
    $watch = Start-Process $Exe -ArgumentList "--watch" -PassThru -NoNewWindow -RedirectStandardOutput $watchLog `
        -RedirectStandardError "$watchLog.err"
    if (-not (Wait-For $watchLog '^state: ready' 60)) { throw "the client never saw ready" }
    Stop-Backend
    if (-not (Wait-For $watchLog '^state: unavailable' 30)) { throw "the client never saw the Backend go" }
    $restarted = Get-Date
    Start-Backend $dir (Join-Path $Out "server-reconnect-2.log")
    if (-not $watch.WaitForExit(60000)) { Stop-Process -Id $watch.Id -Force; Get-Content $watchLog; throw "the client did not come back" }
    $seconds = ((Get-Date) - $restarted).TotalSeconds
    Get-Content $watchLog
    Write-Host ("the client was back {0:N0} seconds after the Backend was restarted" -f $seconds)
    if ($watch.ExitCode -ne 0) { throw "--watch exited $($watch.ExitCode)" }
    Stop-Backend

    Write-Host "== 4. the window, on the desktop, against the live Backend"
    $dir = Join-Path $env:RUNNER_TEMP "pythia-window"
    Start-Backend $dir (Join-Path $Out "server-window.log") "gamesChanged"
    $env:PYTHIA_DATA_DIR = $dir
    $uiLog = Join-Path $Out "ui.log"
    $ui = Start-Process $Exe -ArgumentList "--smoke" -PassThru -RedirectStandardOutput $uiLog -RedirectStandardError "$uiLog.err"
    if (-not (Wait-For $uiLog '^smoke: ' 150)) { Stop-Process -Id $ui.Id -Force; Get-Content $uiLog, "$uiLog.err" -ErrorAction SilentlyContinue; throw "the window never got to smoke" }
    Start-Sleep -Seconds 1
    Save-Screenshot (Join-Path $Out "screen.png")
    if (-not $ui.WaitForExit(60000)) { Stop-Process -Id $ui.Id -Force; throw "the window did not exit" }
    $exit = $ui.ExitCode
    Get-Content $uiLog
    Set-Content (Join-Path $Out "ui.exit") "exit=$exit"
    $log = Get-Content $uiLog -Raw
    foreach ($want in @('ui: rendered hello status=ok', 'ui: rendered get_system_info status=ok', 'smoke: ok')) {
        if ($log -notlike "*$want*") { throw "the window's log has no '$want'" }
    }
    if ($log -notmatch 'ui: rendered events=[1-9]') { throw "no event reached the window" }
    if ($exit -ne 0) { throw "the window exited $exit, not 0" }
    Write-Host "integration: ok"
}
finally {
    Stop-Backend
}
