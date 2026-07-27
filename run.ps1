Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "            QUICKMENU AGENT BOOTSTRAPPER          " -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

# Check virtual environment
$venvPath = "C:\Users\Amonex\QuickMenuAgent\.venv"
if (-not (Test-Path $venvPath)) {
    Write-Error "Virtual environment not found at $venvPath. Please run setup first."
    Exit 1
}

# Start the browser asynchronously after a small delay
Start-Job -ScriptBlock {
    Start-Sleep -Seconds 3
    Write-Host "Opening QuickMenu Agent Dashboard in browser..." -ForegroundColor Green
    Start-Process "http://127.0.0.1:8000"
} | Out-Null

# Start the FastAPI server using Uvicorn
Write-Host "Starting API and Static Content Web Server on http://127.0.0.1:8000..." -ForegroundColor Yellow
& "$venvPath\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

