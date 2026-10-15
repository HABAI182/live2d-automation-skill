param([Parameter(Mandatory=$true)][string]$InstallDirectory)
$ErrorActionPreference = 'Stop'
$EditorRoot = (Resolve-Path -LiteralPath $InstallDirectory).Path
$EditorExe = Join-Path $EditorRoot 'CubismEditor5.exe'
$BridgeDll = Join-Path $EditorRoot 'app\jre\bin\windowsaccessbridge-64.dll'
if (-not (Test-Path -LiteralPath $EditorExe) -or -not (Test-Path -LiteralPath $BridgeDll)) {
    throw 'Expected an official Windows Cubism installation with bundled Java Access Bridge'
}
# Process environment only. No installation/global Java preferences are changed.
$PreviousOptions = $env:JAVA_TOOL_OPTIONS
try {
    $env:JAVA_TOOL_OPTIONS = ($PreviousOptions + ' -Djavax.accessibility.assistive_technologies=com.sun.java.accessibility.AccessBridge').Trim()
    $StartedEditor = Start-Process -FilePath $EditorExe -WorkingDirectory $EditorRoot -WindowStyle Hidden -PassThru
    @{launcher_pid=$StartedEditor.Id; accessibility='enabled for this launch'; authorization='Grant Allow/Edit in Editor if requested'} | ConvertTo-Json
} finally {
    $env:JAVA_TOOL_OPTIONS = $PreviousOptions
}
