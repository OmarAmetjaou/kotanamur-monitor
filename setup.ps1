$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host "Creating Python environment..."
    python -m venv --system-site-packages (Join-Path $ProjectRoot ".venv")
}

& $VenvPython -c "import requests, bs4" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing Python packages..."
    & $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
}

$EnvFile = Join-Path $ProjectRoot ".env"
if (-not (Test-Path -LiteralPath $EnvFile)) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot ".env.example") -Destination $EnvFile
    Write-Host "Created .env. Add your Telegram bot token and chat ID before starting the monitor."
}

Write-Host "Setup complete. Next: follow the Telegram steps in README.md."

