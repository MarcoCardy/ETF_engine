$projectRoot = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $projectRoot "Avvia Analisi ETF.cmd"
if (-not (Test-Path -LiteralPath $launcher)) { throw "Lanciatore non trovato: $launcher" }
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "Analisi ETF.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcher
$shortcut.WorkingDirectory = $projectRoot
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,14"
$shortcut.Save()
Write-Host "Collegamento creato: $shortcutPath"
