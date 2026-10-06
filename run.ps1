# run.ps1 — веб-приложение «Ассистент автосервиса» на локальной LLM (день 27).
# Облачные модели не используются, ключи не нужны.
#
#   .\run.ps1                         # проверить Ollama и модель, запустить приложение, открыть браузер
#   .\run.ps1 -Model qwen2.5:3b       # модель поменьше — быстрее на процессоре
#   .\run.ps1 -Port 8080              # другой порт
#
# Остановить приложение: Ctrl+C в этом окне.
# Если Windows блокирует запуск:  powershell -ExecutionPolicy Bypass -File .\run.ps1

param(
    [string]$Model = "qwen2.5:7b",
    [int]$Port = 8000
)

Set-Location $PSScriptRoot
$api = "http://localhost:11434"

function Find-Python {
    $installed = @(Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | ForEach-Object { $_.FullName })
    foreach ($candidate in @("py", "python") + $installed) {
        try {
            $output = & $candidate --version 2>&1
            if ($output -match "Python \d") { return $candidate }
        }
        catch { }
    }
    return $null
}

function Find-Ollama {
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @("$env:LOCALAPPDATA\Programs\Ollama\ollama.exe", "$env:ProgramFiles\Ollama\ollama.exe")) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Test-Server {
    try { return (Invoke-RestMethod "$api/api/version" -TimeoutSec 3).version } catch { return $null }
}

$py = Find-Python
if (-not $py) { Write-Host "Python не найден. Установите: winget install Python.Python.3.12" -ForegroundColor Red; exit 1 }

$ollama = Find-Ollama
if (-not $ollama) {
    Write-Host "Ollama не найдена. Установите: winget install Ollama.Ollama — и откройте PowerShell заново." -ForegroundColor Red
    exit 1
}

$version = Test-Server
if (-not $version) {
    Write-Host "Запускаю сервер Ollama в фоне..." -ForegroundColor Cyan
    Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
    for ($i = 0; $i -lt 30 -and -not $version; $i++) { Start-Sleep -Seconds 1; $version = Test-Server }
    if (-not $version) { Write-Host "Сервер Ollama не поднялся. Запустите приложение Ollama вручную." -ForegroundColor Red; exit 1 }
}
Write-Host "Ollama $version работает на этом компьютере" -ForegroundColor Green

$names = @((Invoke-RestMethod "$api/api/tags").models | ForEach-Object { $_.name })
if ($names -notcontains $Model -and $names -notcontains "$Model`:latest") {
    Write-Host "Скачиваю модель $Model (один раз)..." -ForegroundColor Cyan
    & $ollama pull $Model
    if ($LASTEXITCODE -ne 0) { Write-Host "Не удалось скачать $Model." -ForegroundColor Red; exit 1 }
}

$url = "http://127.0.0.1:$Port"
Write-Host "`nОткрываю $url в браузере. Остановить приложение: Ctrl+C`n" -ForegroundColor Cyan
Start-Job -ScriptBlock { param($u) Start-Sleep -Seconds 2; Start-Process $u } -ArgumentList $url | Out-Null
& $py .\server.py --model $Model --port $Port
