$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python virtual environment creation failed' }
}
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --windowed --onedir --name Shiyin --collect-all imageio_ffmpeg --add-binary 'qmc_native.dll;.' app.py
if ($LASTEXITCODE -ne 0) { throw 'Application build failed' }
& .\.venv\Scripts\python.exe package_release.py
if ($LASTEXITCODE -ne 0) { throw 'Release packaging failed' }
