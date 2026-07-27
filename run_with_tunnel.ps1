Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "     QUICKMENU AI BOOTSTRAPPER WITH PUBLIC TUNNEL   " -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

$venvPath = "C:\Users\Amonex\QuickMenuAgent\.venv"
if (-not (Test-Path $venvPath)) {
    Write-Error "Virtual environment not found. Please activate your environment first."
    Exit 1
}

# Load .env file if it exists
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

$choice = ""
$domain = $env:NGROK_DOMAIN
$ngrokToken = $env:NGROK_AUTHTOKEN

if ($domain -and $domain -ne "") {
    Write-Host "Found pre-configured Ngrok domain in .env: $domain" -ForegroundColor Green
    $useEnvDomain = Read-Host "Do you want to use this domain to launch the tunnel? [Y/n]"
    if ($useEnvDomain -eq "" -or $useEnvDomain.ToLower() -eq "y") {
        $choice = "1"
    }
}

if ($choice -ne "1") {
    # Ask for tunnel type selection
    Write-Host "Select a permanent public tunneling method:" -ForegroundColor Green
    Write-Host "  1) Ngrok (Supports permanent free custom domains like yoursubdomain.ngrok-free.app)"
    Write-Host "  2) Serveo (Zero-install SSH tunnel, e.g., yoursubdomain.serveo.net)"
    $choice = Read-Host "Enter Choice [1 or 2]"
}

if ($choice -eq "1") {
    # Locate or download Ngrok
    $ngrokCmd = "ngrok"
    if (-not (Get-Command "ngrok" -ErrorAction SilentlyContinue)) {
        if (Test-Path ".\ngrok.exe") {
            $ngrokCmd = (Resolve-Path ".\ngrok.exe").Path
        }
        else {
            Write-Host "Ngrok client not found on system. Downloading ngrok stable v3..." -ForegroundColor Yellow
            $url = "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip"
            Invoke-WebRequest -Uri $url -OutFile "ngrok.zip"
            Write-Host "Extracting ngrok.exe..." -ForegroundColor Yellow
            Expand-Archive -Path "ngrok.zip" -DestinationPath "." -Force
            Remove-Item "ngrok.zip" -ErrorAction SilentlyContinue
            $ngrokCmd = (Resolve-Path ".\ngrok.exe").Path
            Write-Host "Ngrok installed successfully in your directory!" -ForegroundColor Green
        }
    }
    else {
        $ngrokCmd = (Get-Command "ngrok").Source
    }

    # Ngrok configuration
    if ($ngrokToken -and $ngrokToken -ne "") {
        Write-Host "Setting up Ngrok Authtoken from .env..." -ForegroundColor Green
        & $ngrokCmd config add-authtoken $ngrokToken
    }
    else {
        $ngrokTokenInput = Read-Host "Enter your Ngrok Authtoken (Press Enter to skip if already configured)"
        if ($ngrokTokenInput -ne "") {
            & $ngrokCmd config add-authtoken $ngrokTokenInput
        }
    }
    
    if (-not $domain -or $domain -eq "") {
        $domain = Read-Host "Enter your permanent Ngrok domain (e.g. customized-subdomain.ngrok-free.app)"
    }
    
    if ($domain -eq "") {
        Write-Error "Ngrok domain is required for a permanent link."
        Exit 1
    }
    
    # Start Ngrok Tunnel in background using absolute path
    Start-Job -ScriptBlock {
        param($cmd, $d)
        Write-Host "Starting Ngrok tunnel to domain $d on port 8000..." -ForegroundColor Green
        & $cmd http 8000 --domain=$d
    } -ArgumentList $ngrokCmd, $domain | Out-Null
    
    Write-Host "Tunnel starting! Your dashboard will be live at: https://$domain" -ForegroundColor Yellow

}
elseif ($choice -eq "2") {
    # Serveo configuration
    $subdomain = Read-Host "Enter your desired subdomain name (e.g. quickmenuagent-naman)"
    if ($subdomain -eq "") {
        Write-Error "Subdomain name is required."
        Exit 1
    }
    
    # Start SSH Tunnel in background
    Start-Job -ScriptBlock {
        param($sub)
        Write-Host "Starting Serveo tunnel on port 8000..." -ForegroundColor Green
        & ssh -o StrictHostKeyChecking=no -R ${sub}:80:localhost:8000 serveo.net
    } -ArgumentList $subdomain | Out-Null
    
    Write-Host "Tunnel starting! Your dashboard will be live at: https://$subdomain.serveo.net" -ForegroundColor Yellow

}
else {
    Write-Host "Starting FastAPI server locally only on http://127.0.0.1:8000" -ForegroundColor Red
}

# 2. Start FastAPI Server
Write-Host "Starting FastAPI server on http://127.0.0.1:8000..." -ForegroundColor Green
& "$venvPath\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
