#Requires -Version 5.1
param(
    [ValidateSet("start", "configure", "stop", "restart", "status", "logs", "help")]
    [string]$Action = "start",
    [switch]$NoBrowser,
    [switch]$SkipFirewall
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

function Fail([string]$message) {
    Write-Host ""
    Write-Host "错误：$message" -ForegroundColor Red
    exit 1
}

function Require-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Fail "未安装 Docker Desktop。请先安装并启动 Docker Desktop。"
    }
    & docker compose version *> $null
    if ($LASTEXITCODE -ne 0) { Fail "当前 Docker 没有 Compose v2；请确认 docker compose 命令可用。" }
    & docker info *> $null
    if ($LASTEXITCODE -eq 0) { return }

    $dockerDesktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (-not (Test-Path $dockerDesktop)) {
        Fail "Docker 服务未运行，并且找不到 Docker Desktop。请先安装或手动启动 Docker Desktop。"
    }

    Write-Host "Docker Desktop 尚未运行，正在自动启动……"
    Start-Process $dockerDesktop | Out-Null
    for ($attempt = 0; $attempt -lt 90; $attempt += 1) {
        Start-Sleep -Seconds 2
        & docker info *> $null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Docker Desktop 已就绪。"
            return
        }
    }
    Fail "等待 Docker Desktop 启动超时。请确认虚拟化与 Docker Desktop 状态后重试。"
}

function Get-EnvValue([string]$key) {
    if (-not (Test-Path ".env")) { return "" }
    $line = Get-Content ".env" | Where-Object { $_.StartsWith("$key=") } | Select-Object -First 1
    if (-not $line) { return "" }
    return $line.Substring($key.Length + 1)
}

function Set-EnvValue([string]$key, [string]$value) {
    $lines = [System.Collections.Generic.List[string]]::new()
    $found = $false
    foreach ($line in Get-Content ".env") {
        if ($line.StartsWith("$key=")) {
            $lines.Add("$key=$value")
            $found = $true
        } else {
            $lines.Add($line)
        }
    }
    if (-not $found) { $lines.Add("$key=$value") }
    [System.IO.File]::WriteAllLines(
        (Join-Path $root ".env"),
        $lines,
        [System.Text.UTF8Encoding]::new($false)
    )
}

function New-RandomSecret {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    } finally {
        $generator.Dispose()
    }
    return -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

function Prepare-Env {
    if (-not (Test-Path ".env")) {
        Copy-Item ".env.example" ".env"
        Write-Host "已从 .env.example 创建仅供服务器使用的 .env。"
    }

    $databasePassword = Get-EnvValue "POSTGRES_PASSWORD"
    if (-not $databasePassword -or $databasePassword -eq "change-this-password") {
        Set-EnvValue "POSTGRES_PASSWORD" (New-RandomSecret)
        Write-Host "已为 PostgreSQL 生成随机密码（只保存在 .env，不会输出）。"
    }

    if (-not (Get-EnvValue "KNOWLEDGEDEBT_ACCESS_TOKEN")) {
        Set-EnvValue "KNOWLEDGEDEBT_ACCESS_TOKEN" (New-RandomSecret)
        Write-Host "已为后端生成随机访问令牌（只保存在 .env，不会输出）。"
    }
}

function Read-SecretText([string]$prompt) {
    $secure = Read-Host $prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Configure-AiProvider([switch]$Force) {
    $configured = Get-EnvValue "KNOWLEDGEDEBT_SERVER_AI_CONFIGURED"
    $existingKey = Get-EnvValue "OPENAI_API_KEY"
    if (-not $Force -and ($configured -eq "true" -or $existingKey)) {
        if ($existingKey -and $configured -ne "true") {
            Set-EnvValue "KNOWLEDGEDEBT_SERVER_AI_CONFIGURED" "true"
        }
        return
    }

    Write-Host ""
    Write-Host "========== 首次 AI 服务配置 ==========" -ForegroundColor Cyan
    Write-Host "API Key 只保存在 GMK G10 的 .env 中，不会发送给浏览器。"
    Write-Host "1. OpenCode Go（推荐：使用 OpenCode Go Key）"
    Write-Host "2. OpenAI"
    Write-Host "3. DeepSeek"
    Write-Host "4. OpenCode Zen"
    Write-Host "5. 自定义 OpenAI-compatible 服务"
    Write-Host "0. 暂不配置，使用本地规则引擎"

    do {
        $choice = (Read-Host "请选择 [1]").Trim()
        if (-not $choice) { $choice = "1" }
    } while ($choice -notin @("0", "1", "2", "3", "4", "5"))

    if ($choice -eq "0") {
        Set-EnvValue "OPENAI_API_KEY" ""
        Set-EnvValue "KNOWLEDGEDEBT_AI_PROVIDER" "local_rule"
        Set-EnvValue "KNOWLEDGEDEBT_AUTO_TRANSCRIBE" "false"
        Set-EnvValue "KNOWLEDGEDEBT_SERVER_AI_CONFIGURED" "true"
        Write-Host "已选择本地规则引擎；以后可双击 server-configure.bat 配置外部 AI。"
        return
    }

    switch ($choice) {
        "1" {
            $provider = "opencode"
            $baseUrl = "https://opencode.ai/zen/go/v1"
            $model = "deepseek-v4-flash"
            $autoTranscribe = "false"
            $providerLabel = "OpenCode Go"
        }
        "2" {
            $provider = "openai"
            $baseUrl = "https://api.openai.com/v1"
            $model = "gpt-5-mini"
            $autoTranscribe = "true"
            $providerLabel = "OpenAI"
        }
        "3" {
            $provider = "deepseek"
            $baseUrl = "https://api.deepseek.com"
            $model = "deepseek-chat"
            $autoTranscribe = "false"
            $providerLabel = "DeepSeek"
        }
        "4" {
            $provider = "opencode"
            $baseUrl = "https://opencode.ai/zen/v1"
            $model = "deepseek-v4-flash"
            $autoTranscribe = "false"
            $providerLabel = "OpenCode Zen"
        }
        "5" {
            $provider = "custom_openai_compatible"
            $baseUrl = (Read-Host "Base URL（必须为 HTTPS，例如 https://example.com/v1）").Trim().TrimEnd("/")
            if ($baseUrl -notmatch '^https://') { Fail "自定义 Base URL 必须使用 HTTPS。" }
            $model = (Read-Host "模型 ID").Trim()
            if (-not $model) { Fail "模型 ID 不能为空。" }
            $autoTranscribe = "false"
            $providerLabel = "自定义兼容服务"
        }
    }

    $apiKey = Read-SecretText "$providerLabel API Key（输入时不会显示）"
    if (-not $apiKey -or $apiKey.Contains("`r") -or $apiKey.Contains("`n")) {
        Fail "API Key 不能为空或包含换行。"
    }

    Set-EnvValue "OPENAI_API_KEY" $apiKey
    Set-EnvValue "OPENAI_BASE_URL" $baseUrl
    Set-EnvValue "KNOWLEDGEDEBT_AI_PROVIDER" $provider
    Set-EnvValue "KNOWLEDGEDEBT_AI_MODEL" $model
    Set-EnvValue "KNOWLEDGEDEBT_AUTO_TRANSCRIBE" $autoTranscribe
    Set-EnvValue "KNOWLEDGEDEBT_SERVER_AI_CONFIGURED" "true"
    $apiKey = $null

    Write-Host "已配置 $providerLabel / $model。API Key 只保存在服务器端。" -ForegroundColor Green
    if ($autoTranscribe -eq "false") {
        Write-Host "该服务只用于 AI 分析；自动云端语音转写已关闭，录音仍会先可靠保存。" -ForegroundColor Yellow
    }
}

function Get-LanAddress {
    if ($env:KNOWLEDGEDEBT_SERVER_IP) { return $env:KNOWLEDGEDEBT_SERVER_IP }
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

function Write-AccessUrl {
    $webPort = Get-EnvValue "KNOWLEDGEDEBT_WEB_PORT"
    if (-not $webPort) { $webPort = "3000" }
    $bindAddress = Get-EnvValue "KNOWLEDGEDEBT_WEB_BIND_ADDRESS"
    if (-not $bindAddress) { $bindAddress = "0.0.0.0" }
    $lanAddress = Get-LanAddress

    Write-Host ""
    Write-Host "本机访问：http://127.0.0.1:$webPort"
    if ($bindAddress -eq "127.0.0.1" -or $bindAddress -eq "localhost") {
        Write-Host "当前 KNOWLEDGEDEBT_WEB_BIND_ADDRESS=$bindAddress，只允许本机访问。" -ForegroundColor Yellow
    } elseif ($lanAddress) {
        Write-Host "校园网访问：http://${lanAddress}:$webPort" -ForegroundColor Green
    } else {
        Write-Host "校园网访问：http://<GMK G10 的局域网 IPv4>:$webPort" -ForegroundColor Yellow
    }
    Write-Host "防火墙只需放行 TCP $webPort；不要放行 API 8123。"
}

function Wait-UntilReady {
    $webPort = Get-EnvValue "KNOWLEDGEDEBT_WEB_PORT"
    if (-not $webPort) { $webPort = "3000" }
    Write-Host "正在等待 Web 与同源 API 代理就绪……"
    for ($attempt = 0; $attempt -lt 90; $attempt += 1) {
        try {
            $response = Invoke-WebRequest -Uri "http://127.0.0.1:$webPort/api/backend/health" -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                Write-Host "服务已就绪。"
                return
            }
        } catch { }
        Start-Sleep -Seconds 2
    }
    Write-Host "服务尚未通过健康检查，请运行 .\server.bat logs 查看原因。" -ForegroundColor Yellow
}

function Sync-ApplicationSettings {
    $configured = (Get-EnvValue "KNOWLEDGEDEBT_AUTO_TRANSCRIBE").Trim().ToLowerInvariant()
    if (-not $configured) { return }
    $enabled = $configured -notin @("0", "false", "no")
    $headers = @{}
    $accessToken = Get-EnvValue "KNOWLEDGEDEBT_ACCESS_TOKEN"
    if ($accessToken) { $headers["Authorization"] = "Bearer $accessToken" }
    $body = @{ auto_transcribe = $enabled } | ConvertTo-Json -Compress
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:8123/settings/application" `
            -Method Patch -Headers $headers -ContentType "application/json" -Body $body | Out-Null
        Write-Host "已同步录音自动转写设置。"
    } catch {
        Write-Host "未能同步自动转写设置；可在 Web 设置页手动检查。" -ForegroundColor Yellow
    }
}

function Enable-KnowledgeDebtFirewall([string]$port) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    $ruleName = "KnowledgeDebt Web $port (Local Subnet)"
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        if (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue) {
            Write-Host "Windows 防火墙规则已存在：$ruleName"
            return
        }
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP `
            -LocalPort $port -Action Allow -Profile Any -RemoteAddress LocalSubnet | Out-Null
        Write-Host "已在 Windows 防火墙中仅为本地子网放行 TCP $port。"
        return
    }

    Write-Host "需要 Windows 管理员授权以放行校园网访问；即将显示 UAC 确认框。"
    $escapedName = $ruleName.Replace("'", "''")
    $command = "if (-not (Get-NetFirewallRule -DisplayName '$escapedName' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName '$escapedName' -Direction Inbound -Protocol TCP -LocalPort $port -Action Allow -Profile Any -RemoteAddress LocalSubnet | Out-Null }"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    try {
        $process = Start-Process powershell.exe -Verb RunAs -Wait -PassThru `
            -ArgumentList @("-NoProfile", "-EncodedCommand", $encoded)
        if ($process.ExitCode -eq 0) {
            Write-Host "已在 Windows 防火墙中仅为本地子网放行 TCP $port。"
        } else {
            Write-Host "防火墙配置未完成；其他设备可能无法访问。可稍后重新双击 server.bat。" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "用户取消了管理员授权；服务器仍会启动，但其他设备可能被防火墙拦截。" -ForegroundColor Yellow
    }
}

if ($Action -eq "help") {
    Write-Host "知债 KnowledgeDebt 寝室服务器管理脚本（Docker Compose）"
    Write-Host "用法：server.bat [start|configure|stop|restart|status|logs]"
    Write-Host "直接双击 server.bat：首次配置 API Key、启动服务、配置本地子网防火墙。"
    exit 0
}

Require-Docker

switch ($Action) {
    "start" {
        Prepare-Env
        Configure-AiProvider
        & docker compose up --build --detach
        if ($LASTEXITCODE -ne 0) { Fail "Docker Compose 启动失败。" }
        $port = Get-EnvValue "KNOWLEDGEDEBT_WEB_PORT"
        if (-not $port) { $port = "3000" }
        if (-not $SkipFirewall) {
            Enable-KnowledgeDebtFirewall $port
        }
        Wait-UntilReady
        Sync-ApplicationSettings
        Write-AccessUrl
        if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$port" }
    }
    "configure" {
        Prepare-Env
        Configure-AiProvider -Force
        & docker compose up --build --detach --force-recreate
        if ($LASTEXITCODE -ne 0) { Fail "保存配置后重启服务失败。" }
        $port = Get-EnvValue "KNOWLEDGEDEBT_WEB_PORT"
        if (-not $port) { $port = "3000" }
        if (-not $SkipFirewall) {
            Enable-KnowledgeDebtFirewall $port
        }
        Wait-UntilReady
        Sync-ApplicationSettings
        Write-AccessUrl
        if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$port" }
    }
    "restart" {
        Prepare-Env
        Configure-AiProvider
        & docker compose up --build --detach --force-recreate
        if ($LASTEXITCODE -ne 0) { Fail "Docker Compose 重启失败。" }
        $port = Get-EnvValue "KNOWLEDGEDEBT_WEB_PORT"
        if (-not $port) { $port = "3000" }
        if (-not $SkipFirewall) {
            Enable-KnowledgeDebtFirewall $port
        }
        Wait-UntilReady
        Sync-ApplicationSettings
        Write-AccessUrl
        if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$port" }
    }
    "stop" {
        & docker compose down
        if ($LASTEXITCODE -ne 0) { Fail "Docker Compose 停止失败。" }
        Write-Host "服务已停止；PostgreSQL、资源和模型数据卷均已保留。"
    }
    "status" {
        & docker compose ps
        Write-AccessUrl
    }
    "logs" {
        & docker compose logs --follow web backend database
    }
}
