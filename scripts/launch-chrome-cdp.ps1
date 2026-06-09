<#
.SYNOPSIS
    Arranca Google Chrome con remote debugging (CDP) abierto para que WSO
    pueda attachearse vía Playwright.

.DESCRIPTION
    Puerto default 9222 (coincide con WSO_BROWSER_CDP_URL default).
    Por default usa un perfil aislado en %USERPROFILE%\.wso-chrome.
    Con -Default usa tu perfil real de Chrome (cerrá las otras ventanas primero).

.EXAMPLE
    .\scripts\launch-chrome-cdp.ps1
    .\scripts\launch-chrome-cdp.ps1 -Default
#>
param(
    [switch]$Default,
    [int]$CdpPort = 9222
)

$ErrorActionPreference = "Stop"

# Detectar el binario de Chrome.
$candidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LocalAppData\Google\Chrome\Application\chrome.exe"
)
$chrome = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $chrome) {
    Write-Error "No encontré chrome.exe. Editá la lista de rutas en este script."
    exit 1
}

$chromeArgs = @(
    "--remote-debugging-port=$CdpPort",
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check"
)

if (-not $Default) {
    $profileDir = if ($env:WSO_CHROME_PROFILE) { $env:WSO_CHROME_PROFILE } else { "$env:USERPROFILE\.wso-chrome" }
    New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
    $chromeArgs += "--user-data-dir=$profileDir"
    Write-Host "-> Perfil aislado: $profileDir"
} else {
    Write-Host "-> Perfil DEFAULT de Chrome (tus sesiones reales). Cerra las otras ventanas de Chrome primero."
}

Write-Host "-> Lanzando Chrome con CDP en http://localhost:$CdpPort"
Write-Host "-> Deja esta ventana de Chrome abierta y corre el spike en otra terminal."
& $chrome @chromeArgs
