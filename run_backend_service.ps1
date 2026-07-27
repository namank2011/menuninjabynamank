# Load env
if (Test-Path .env) {
    Get-Content .env | Where-Object { $_ -and -not $_.StartsWith("#") } | ForEach-Object {
        $name, $value = $_.Split('=', 2)
        if ($name -and $value) {
            $nameClean = $name.Trim()
            $valueClean = $value.Trim()
            [Environment]::SetEnvironmentVariable($nameClean, $valueClean, "Process")
        }
    }
}

$domain = $env:NGROK_DOMAIN
$ngrokToken = $env:NGROK_AUTHTOKEN

# Add Authtoken to ngrok
if ($ngrokToken) {
    Write-Host "Setting ngrok authtoken..."
    & ".\ngrok.exe" config add-authtoken $ngrokToken
}

# Terminate any running instances of ngrok or python uvicorn
Stop-Process -Name "ngrok", "python" -Force -ErrorAction SilentlyContinue

# Start ngrok helper
Start-Job -ScriptBlock {
    & "c:\Users\Amonex\QuickMenuAgent\ngrok.exe" http 8000 --domain=croak-krypton-sculptor.ngrok-free.dev
} | Out-Null

Write-Host "Ngrok tunnel launched at: https://croak-krypton-sculptor.ngrok-free.dev" -ForegroundColor Green

# Start uvicorn
Write-Host "Starting FastAPI server..." -ForegroundColor Green
& "C:\Users\Amonex\QuickMenuAgent\.venv\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
