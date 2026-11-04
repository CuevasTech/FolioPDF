# Checks the Folio app as people get it: opens a small PDF and waits until the
# window has drawn its page, with the document engine running sandboxed. Used by
# the GitHub build on Windows x64 and on Windows 11 on Arm, where the x64 app
# runs under emulation.
#
#   pwsh -File scripts\app-check.ps1 -Zip dist\Folio-Portable.zip [-Screenshot app.png] [-ExpectEmulated]
#   pwsh -File scripts\app-check.ps1 -Setup dist\Folio-Setup.exe [-Screenshot app.png] [-ExpectEmulated]
#
# -Zip unzips the portable version in a new temporary folder and runs it there.
# -Setup installs Folio for the current user (silently), runs it, uninstalls it
# and checks that nothing is left. It changes the system, so it only runs on
# GitHub's build machines, which are thrown away after each build.
# -Screenshot captures only the Folio window (PrintWindow), never the rest of
# the screen. Everything started here is closed and deleted at the end.
# ASCII only: Windows PowerShell 5.1 reads scripts without a BOM as ANSI.
param(
    [string] $Zip,
    [string] $Setup,
    [string] $Screenshot,
    [switch] $ExpectEmulated,
    [int] $TimeoutSeconds = 180
)
$ErrorActionPreference = 'Stop'

function Get-FullPath([string] $path) {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($path)
}

# A one-page PDF written here, so the check needs no sample files.
function New-TestPdf([string] $path) {
    $content = 'BT /F1 32 Tf 72 740 Td (Folio app check) Tj ET'
    $objects = @(
        '<< /Type /Catalog /Pages 2 0 R >>',
        '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        "<< /Length $($content.Length) >>`nstream`n$content`nendstream"
    )
    $text = [System.Text.StringBuilder]::new("%PDF-1.7`n")
    $offsets = @()
    for ($i = 0; $i -lt $objects.Count; $i++) {
        $offsets += $text.Length  # ASCII: one character is one byte
        [void]$text.Append("$($i + 1) 0 obj`n$($objects[$i])`nendobj`n")
    }
    $xref = $text.Length
    [void]$text.Append("xref`n0 $($objects.Count + 1)`n0000000000 65535 f`r`n")
    foreach ($offset in $offsets) { [void]$text.Append(('{0:D10} 00000 n' -f $offset) + "`r`n") }
    [void]$text.Append("trailer`n<< /Size $($objects.Count + 1) /Root 1 0 R >>`nstartxref`n$xref`n%%EOF`n")
    [System.IO.File]::WriteAllText($path, $text.ToString(), [System.Text.Encoding]::ASCII)
}

function Read-Log([string] $path) {
    if (Test-Path $path) { Get-Content $path -Raw -Encoding utf8 } else { '' }
}

# Captures the Folio window alone and fails if it is blank (one flat colour):
# that is how a window looks when the app runs but nothing is painted.
function Save-WindowShot([System.Diagnostics.Process] $process, [string] $path) {
    Add-Type -AssemblyName System.Drawing
    if (-not ('FolioCheck.Native' -as [type])) {
        Add-Type -Namespace FolioCheck -Name Native -MemberDefinition @'
[System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
public struct Rect { public int Left, Top, Right, Bottom; }
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool GetWindowRect(System.IntPtr window, out Rect rect);
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool PrintWindow(System.IntPtr window, System.IntPtr hdc, uint flags);
'@
    }
    $process.Refresh()
    $window = $process.MainWindowHandle
    if ($window -eq [System.IntPtr]::Zero) { throw 'Folio has no window.' }
    $rect = New-Object FolioCheck.Native+Rect
    if (-not [FolioCheck.Native]::GetWindowRect($window, [ref] $rect)) { throw 'Could not read the window size.' }
    $width = $rect.Right - $rect.Left
    $height = $rect.Bottom - $rect.Top
    if ($width -lt 200 -or $height -lt 200) { throw "The window is too small ($width x $height)." }
    $bitmap = New-Object System.Drawing.Bitmap $width, $height
    try {
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        $hdc = $graphics.GetHdc()
        # 2 = PW_RENDERFULLCONTENT, needed for windows drawn by the GPU (Chromium).
        $printed = [FolioCheck.Native]::PrintWindow($window, $hdc, 2)
        $graphics.ReleaseHdc($hdc)
        $graphics.Dispose()
        if (-not $printed) { throw 'Could not capture the window.' }
        $bitmap.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
        $colours = New-Object 'System.Collections.Generic.HashSet[int]'
        for ($x = 0; $x -lt 24; $x++) {
            for ($y = 0; $y -lt 24; $y++) {
                [void]$colours.Add($bitmap.GetPixel([int](($x + 0.5) * $width / 24), [int](($y + 0.5) * $height / 24)).ToArgb())
            }
        }
    }
    finally { $bitmap.Dispose() }
    "window: $width x $height, $($colours.Count) colours sampled, saved to $path"
    if ($colours.Count -lt 3) { throw 'The Folio window is blank.' }
}

function Stop-Folio([System.Diagnostics.Process] $process) {
    if ($process -and -not $process.HasExited) {
        # Only the processes started here: Folio and the ones it launched.
        Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $process.Id } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 800
}

# Starts Folio with the PDF, waits for the page and checks the log. -Arguments
# go before the PDF (for example a temporary profile).
function Test-Folio([string] $exe, [string] $pdf, [string] $log, [string[]] $arguments, [bool] $portable) {
    $started = Get-Date
    $process = Start-Process -FilePath $exe -ArgumentList (@($arguments) + @("`"$pdf`"")) -PassThru
    try {
        $deadline = $started.AddSeconds($TimeoutSeconds)
        while ((Get-Date) -lt $deadline -and -not $process.HasExited) {
            Start-Sleep -Milliseconds 700
            if ((Read-Log $log) -match 'primera p.gina dibujada') { break }
        }
        $seconds = [int]((Get-Date) - $started).TotalSeconds
        $text = Read-Log $log
        $text -split "`r?`n" | Where-Object { $_ } | ForEach-Object { "  $_" }

        if ($process.HasExited) { throw "Folio closed by itself (exit code $($process.ExitCode))." }
        if ($portable -and $text -notmatch 'iniciado \(portable\)') { throw 'Folio did not start as the portable version.' }
        if (-not $portable -and $text -notmatch 'iniciado') { throw 'Folio did not start.' }
        if ($text -match 'funciona sin aislamiento') { throw 'The document engine is not sandboxed.' }
        if ($text -notmatch 'documento abierto \(1 p') { throw "The PDF did not open within $TimeoutSeconds s." }
        if ($text -notmatch 'primera p.gina dibujada') { throw "The window did not draw the page within $TimeoutSeconds s." }
        $engine = Get-CimInstance Win32_Process -Filter "Name = 'folio-engine.exe'" | Where-Object { $_.ParentProcessId -eq $process.Id }
        if (-not $engine) { throw 'The document engine is not running.' }
        if ($ExpectEmulated -and $text -notmatch 'emulado') { throw 'The engine did not report x64 emulation on Arm.' }
        if ($Screenshot) {
            Start-Sleep -Seconds 3  # let the page finish painting
            Save-WindowShot $process $Screenshot
        }
        "app: started, PDF opened and page drawn in $seconds s, engine sandboxed"
    }
    finally { Stop-Folio $process }
}

if ([bool]$Zip -eq [bool]$Setup) { throw 'Give either -Zip or -Setup.' }
if ($Setup -and $env:GITHUB_ACTIONS -ne 'true') {
    throw 'The installer check installs Folio for the current user: it only runs on GitHub build machines.'
}
if ($Screenshot) { $Screenshot = Get-FullPath $Screenshot }

$folder = Join-Path ([System.IO.Path]::GetTempPath()) ('folio-app-check-' + [System.Guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $folder | Out-Null
try {
    $pdf = Join-Path $folder 'check.pdf'
    New-TestPdf $pdf
    if ($Zip) {
        $Zip = Get-FullPath $Zip
        if (-not (Test-Path $Zip)) { throw "No zip at $Zip." }
        $app = Join-Path $folder 'app'
        New-Item -ItemType Directory -Path $app | Out-Null
        # Windows' own tar, fixed arguments, no shell.
        & "$env:SystemRoot\System32\tar.exe" -x -f $Zip -C $app
        if ($LASTEXITCODE -ne 0) { throw 'Could not unzip the portable version.' }
        if (-not (Test-Path (Join-Path $app 'Folio.exe'))) { throw 'The zip has no Folio.exe.' }
        Test-Folio (Join-Path $app 'Folio.exe') $pdf (Join-Path $app 'FolioData\logs\folio.log') @() $true
    }
    else {
        $Setup = Get-FullPath $Setup
        if (-not (Test-Path $Setup)) { throw "No installer at $Setup." }
        $installed = Join-Path $env:LOCALAPPDATA 'Programs\Folio'
        $setupRun = Start-Process -FilePath $Setup -ArgumentList @('/S', '/currentuser') -Wait -PassThru
        if ($setupRun.ExitCode -ne 0) { throw "The installer failed (exit code $($setupRun.ExitCode))." }
        $exe = Join-Path $installed 'Folio.exe'
        if (-not (Test-Path $exe)) { throw "Folio was not installed in $installed." }
        $registered = Get-ItemProperty 'HKCU:\Software\RegisteredApplications' -Name Folio -ErrorAction SilentlyContinue
        if (-not $registered) { throw 'The installer did not register Folio as a PDF app.' }
        "installer: installed in $installed and registered"
        $stray = { Get-Process Folio -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$installed\*" } | Stop-Process -Force }
        & $stray  # in case the installer opened Folio itself

        # A temporary profile: the check never reads or writes Folio's usual one.
        $userData = Join-Path $folder 'profile'
        Test-Folio $exe $pdf (Join-Path $userData 'logs\folio.log') @("--user-data-dir=`"$userData`"") $false

        & $stray
        Start-Sleep -Seconds 1
        $uninstaller = Join-Path $installed 'Uninstall Folio.exe'
        $removeRun = Start-Process -FilePath $uninstaller -ArgumentList @('/S', '/currentuser') -Wait -PassThru
        if ($removeRun.ExitCode -ne 0) { throw "The uninstaller failed (exit code $($removeRun.ExitCode))." }
        # The uninstaller copies itself to a temporary folder and goes on from there.
        $left = { (Test-Path $exe) -or (Test-Path 'HKCU:\Software\CuevasTech\Folio') -or
            [bool](Get-ItemProperty 'HKCU:\Software\RegisteredApplications' -Name Folio -ErrorAction SilentlyContinue) }
        $deadline = (Get-Date).AddSeconds(120)
        while ((Get-Date) -lt $deadline -and (& $left)) { Start-Sleep -Seconds 1 }
        if (Test-Path $exe) { throw 'Folio is still installed after uninstalling.' }
        if (& $left) { throw 'The uninstaller left Folio registered in the registry.' }
        'installer: uninstalled, nothing left in the registry'
    }
}
finally {
    Remove-Item -Recurse -Force $folder -ErrorAction SilentlyContinue
}
