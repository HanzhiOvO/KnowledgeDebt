#Requires -Version 5.1
param(
    [switch]$NoBrowser,
    [switch]$SkipInstall,
    [switch]$Lan,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

function Write-Step([string]$message) {
    Write-Host "==> $message"
}

function Fail([string]$message) {
    Write-Host ""
    Write-Host "ERROR: $message" -ForegroundColor Red
    Write-Host ""
    exit 1
}

function Get-CompatiblePython {
    $candidates = @()
    if ($env:PYTHON_BIN) { $candidates += $env:PYTHON_BIN }
    $candidates += @("python", "py")

    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        try {
            & $command.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return $command.Source
            }
        } catch {
            continue
        }
    }

    Fail "未找到 Python 3.12 或更高版本。请先安装 Python 3.12+ 并勾选 Add python.exe to PATH；也可以用 PYTHON_BIN 环境变量指定完整路径。"
}

function Get-CombinedHash([string[]]$files) {
    $parts = foreach ($file in $files) {
        (Get-FileHash -Path $file -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $text = $parts -join "|"
    $stream = [System.IO.MemoryStream]::new([System.Text.Encoding]::UTF8.GetBytes($text))
    try {
        return (Get-FileHash -InputStream $stream -Algorithm SHA256).Hash.ToLowerInvariant()
    } finally {
        $stream.Dispose()
    }
}

function Stop-Tree($process) {
    if ($process -and (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)) {
        & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
    }
}

function Get-LanAddress {
    try {
        return Get-NetIPConfiguration |
            Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" } |
            ForEach-Object { $_.IPv4Address.IPAddress } |
            Where-Object { $_ -and $_ -notlike "169.254.*" } |
            Select-Object -First 1
    } catch {
        return $null
    }
}

if ($Help) {
    Write-Host "知债 KnowledgeDebt - Windows 一键启动"
    Write-Host "用法: start.bat [-Lan] [-NoBrowser] [-SkipInstall]"
    Write-Host "  -Lan 允许同一局域网 / 校园网内的设备访问 Web（仅开放 3000）"
    exit 0
}

# ----------------------------------------------------------------
# 基础环境检查
# ----------------------------------------------------------------
Write-Step "检查 Node.js / npm"
$node = Get-Command node -ErrorAction SilentlyContinue
$npm = Get-Command npm -ErrorAction SilentlyContinue
if (-not $node) { Fail "未找到 Node.js。请先安装 Node.js 24 或更高版本。" }
if (-not $npm) { Fail "未找到 npm。请重新安装 Node.js，确保 npm 在 PATH 中。" }

$nodeMajor = 0
try {
    $nodeMajor = [int](& node -p "Number(process.versions.node.split('.')[0])")
} catch { }

if ($nodeMajor -lt 24) {
    Fail "需要 Node.js 24 或更高版本，当前版本为 $(& node --version)。"
}

Write-Step "检查 Python 3.12+"
$python = Get-CompatiblePython
Write-Host "    使用 Python: $python"

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Step "已创建 .env。AI Provider 可在 Web 设置页粘贴官方 Key；语音转写默认使用本地 Whisper。"
}

$env:npm_config_cache = Join-Path $root ".npm-cache"

# ----------------------------------------------------------------
# 依赖安装
# ----------------------------------------------------------------
if (-not $SkipInstall) {
    $venvPython = Join-Path $root ".venv\Scripts\python.exe"

    if (-not (Test-Path $venvPython)) {
        Write-Step "正在创建 Python 虚拟环境……"
        & $python -m venv ".venv"
        if ($LASTEXITCODE -ne 0) {
            Fail "创建 .venv 失败。请确认 Python 安装中包含 venv 模块。"
        }
    }

    $requirementFiles = @("backend\requirements.txt", "backend\requirements-dev.txt")
    $requirementsStamp = ".venv\.knowledgedebt-requirements.sha256"
    $requirementsHash = Get-CombinedHash $requirementFiles
    $installedRequirementsHash = if (Test-Path $requirementsStamp) {
        (Get-Content $requirementsStamp -TotalCount 1).Trim()
    } else { "" }

    if ($requirementsHash -ne $installedRequirementsHash) {
        Write-Step "正在安装后端依赖……"
        & $venvPython -m pip install -r "backend\requirements-dev.txt"
        if ($LASTEXITCODE -ne 0) { Fail "后端依赖安装失败。" }
        Set-Content -Path $requirementsStamp -Value $requirementsHash -Encoding ASCII
    } else {
        Write-Step "后端依赖没有变化，跳过安装。"
    }

    $webStamp = "web\node_modules\.knowledgedebt-package-lock.sha256"
    $webHash = Get-CombinedHash @("web\package.json", "web\package-lock.json")
    $installedWebHash = if (Test-Path $webStamp) {
        (Get-Content $webStamp -TotalCount 1).Trim()
    } else { "" }

    if (-not (Test-Path "web\node_modules") -or $webHash -ne $installedWebHash) {
        Write-Step "正在安装 Web 依赖……"
        Push-Location "web"
        try {
            & cmd.exe /c "npm ci"
            if ($LASTEXITCODE -ne 0) { Fail "Web 依赖安装失败。" }
        } finally {
            Pop-Location
        }
        Set-Content -Path $webStamp -Value $webHash -Encoding ASCII
    } else {
        Write-Step "Web 依赖没有变化，跳过安装。"
    }
} else {
    $venvPython = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) { Fail "未找到 .venv；请去掉 -SkipInstall 后重试。" }
    if (-not (Test-Path "web\node_modules")) { Fail "未找到 web\node_modules；请去掉 -SkipInstall 后重试。" }
}

# ----------------------------------------------------------------
# 启动服务
# ----------------------------------------------------------------
Write-Step "正在启动知债（KnowledgeDebt）……"
Write-Host "Web: http://localhost:3000"
Write-Host "API: http://127.0.0.1:8123"
$webHost = if ($Lan) { "0.0.0.0" } else { "127.0.0.1" }
if ($Lan) {
    $lanAddress = Get-LanAddress
    if ($lanAddress) {
        Write-Host "校园网访问地址: http://${lanAddress}:3000" -ForegroundColor Green
    } else {
        Write-Host "校园网访问地址: http://<这台电脑的局域网 IPv4>:3000" -ForegroundColor Yellow
    }
    Write-Host "已仅向局域网开放 Web 3000；API 8123 仍只允许本机访问。"
    Write-Host "请只在防火墙中放行 TCP 3000，不要放行 8123。" -ForegroundColor Yellow
}
Write-Host "日志: start-api.log / start-web.log"
Write-Host "按 Ctrl+C 可同时停止服务。"

$api = $null
$web = $null
try {
    $api = Start-Process -FilePath $venvPython `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--reload", "--host", "127.0.0.1", "--port", "8123") `
        -WorkingDirectory (Join-Path $root "backend") `
        -RedirectStandardOutput (Join-Path $root "start-api.log") `
        -RedirectStandardError (Join-Path $root "start-api.error.log") `
        -PassThru -WindowStyle Hidden

    $web = Start-Process -FilePath "cmd.exe" `
        -ArgumentList @("/c", "npm run dev -- --hostname $webHost") `
        -WorkingDirectory (Join-Path $root "web") `
        -RedirectStandardOutput (Join-Path $root "start-web.log") `
        -RedirectStandardError (Join-Path $root "start-web.error.log") `
        -PassThru -WindowStyle Hidden

    if (-not $NoBrowser) {
        Write-Step "等待 Web 服务就绪……"
        $ready = $false
        for ($attempt = 0; $attempt -lt 90; $attempt += 1) {
            if (-not (Get-Process -Id $api.Id -ErrorAction SilentlyContinue)) { break }
            if (-not (Get-Process -Id $web.Id -ErrorAction SilentlyContinue)) { break }
            try {
                $response = Invoke-WebRequest -Uri "http://127.0.0.1:3000" -UseBasicParsing -TimeoutSec 2
                if ($response.StatusCode -eq 200) {
                    $ready = $true
                    break
                }
            } catch { }
            Start-Sleep -Seconds 1
        }
        if ($ready) {
            Write-Host ""
            Write-Step "服务已就绪，正在打开浏览器……"
            Start-Process "http://localhost:3000"
        } else {
            Write-Host ""
            Write-Host "服务仍在启动，请稍后手动打开 http://localhost:3000" -ForegroundColor Yellow
        }
    }

    while ((Get-Process -Id $api.Id -ErrorAction SilentlyContinue) -and (Get-Process -Id $web.Id -ErrorAction SilentlyContinue)) {
        Start-Sleep -Seconds 2
    }
} finally {
    Write-Host ""
    Write-Step "正在停止服务……"
    Stop-Tree $api
    Stop-Tree $web
}

if (Test-Path "start-api.error.log") {
    $apiError = Get-Content "start-api.error.log" -Tail 20
    if ($apiError) {
        Write-Host ""
        Write-Host "后端日志尾部:" -ForegroundColor Yellow
        $apiError | ForEach-Object { Write-Host $_ }
    }
}
if (Test-Path "start-web.error.log") {
    $webError = Get-Content "start-web.error.log" -Tail 20
    if ($webError) {
        Write-Host ""
        Write-Host "Web 日志尾部:" -ForegroundColor Yellow
        $webError | ForEach-Object { Write-Host $_ }
    }
}

Write-Host ""
Write-Step "服务已停止。"
