# Empaqueta el widget a un .exe único (sin consola) y lo agrega al inicio de Windows.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

py -m pip install --quiet pyinstaller pystray Pillow requests  # ponytail: no redirigir stderr; en PS 5.1 2>$null lo envuelve en NativeCommandError y aborta con -Stop
py -m PyInstaller --noconsole --onefile --clean --name "Tokei" --icon "Tokei.ico" tokei.py

$exe = Join-Path $PSScriptRoot "dist\Tokei.exe"
if (-not (Test-Path $exe)) { throw "No se generó el .exe" }

$startup = [Environment]::GetFolderPath('Startup')
$lnk = Join-Path $startup "Tokei.lnk"
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($lnk)
$s.TargetPath = $exe
$s.WorkingDirectory = (Split-Path $exe)
$s.Save()

Write-Host "Listo: $exe"
Write-Host "Autostart: $lnk"
Write-Host "Arráncalo ahora con doble clic o ejecutando el .exe."
