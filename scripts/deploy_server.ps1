# kanban 金融数据看板 - Windows服务器一键部署
# 用法: 服务器上以【管理员】打开 PowerShell, 执行:
#   powershell -ExecutionPolicy Bypass -File deploy_server.ps1 [-Port 8080]
# 完成后访问 http://服务器IP:端口/
param(
    [int]$Port = 8080,
    [string]$Root = "C:\kanban"
)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Write-Host "==== kanban 看板部署 ====" -ForegroundColor Cyan

# ---------- 1. 站点代码 ----------
$zip = "$env:TEMP\kanban-main.zip"
$srcUrls = @(
    "https://codeload.github.com/zkkwyy/kanban/zip/refs/heads/main",
    "https://ghproxy.net/https://github.com/zkkwyy/kanban/archive/refs/heads/main.zip",
    "https://gh-proxy.com/https://github.com/zkkwyy/kanban/archive/refs/heads/main.zip"
)
$ok = $false
foreach ($u in $srcUrls) {
    try {
        Write-Host "下载站点: $u"
        Invoke-WebRequest $u -OutFile $zip -UseBasicParsing -TimeoutSec 120
        if ((Get-Item $zip).Length -gt 100KB) { $ok = $true; break }
    } catch { Write-Host "  失败: $($_.Exception.Message)" -ForegroundColor Yellow }
}
if (-not $ok) {
    # 服务器根证书过老时忽略校验重试(仅下载部署包用)
    Write-Host "常规下载失败, 忽略证书校验重试..." -ForegroundColor Yellow
    [Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
    foreach ($u in $srcUrls) {
        try {
            Invoke-WebRequest $u -OutFile $zip -UseBasicParsing -TimeoutSec 120
            if ((Get-Item $zip).Length -gt 100KB) { $ok = $true; break }
        } catch { Write-Host "  失败: $($_.Exception.Message)" -ForegroundColor Yellow }
    }
    [Net.ServicePointManager]::ServerCertificateValidationCallback = $null
}
if (-not $ok) { throw "站点下载失败: 服务器需能访问 GitHub 或镜像之一" }
if (Test-Path "$Root.new") { Remove-Item "$Root.new" -Recurse -Force }
Expand-Archive $zip -DestinationPath "$Root.new" -Force
$inner = Get-ChildItem "$Root.new" -Directory | Where-Object Name -like "kanban-*" | Select-Object -First 1
if (Test-Path "$Root\data") {
    Copy-Item "$Root\data\*" "$($inner.FullName)\data\" -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path "$Root.old") { Remove-Item "$Root.old" -Recurse -Force }
    Rename-Item $Root "kanban.old" -ErrorAction SilentlyContinue
}
Move-Item $inner.FullName $Root -Force
Remove-Item "$Root.new", "$Root.old" -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "站点就位: $Root"

# ---------- 2. 便携Python(3.11.9 embed) ----------
$py = "$Root\python\python.exe"
if (-not (Test-Path $py)) {
    $pyzip = "$env:TEMP\python-embed.zip"
    try {
        Invoke-WebRequest "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip" `
            -OutFile $pyzip -UseBasicParsing -TimeoutSec 300
    } catch { throw "Python下载失败: $($_.Exception.Message)" }
    Expand-Archive $pyzip -DestinationPath "$Root\python" -Force
}
# embed解锁标准库与site导入
$pth = "$Root\python\python311._pth"
if (Test-Path $pth) {
    (Get-Content $pth) -replace "^#\s*import\s+site", "import site" | Set-Content $pth
}
Write-Host "Python: $py"
# 自检(含时区: tzdata已随仓库vendor进scripts\tzdata)
& $py -c "import zoneinfo as z,sys; sys.path.insert(0,r'$Root\scripts'); z.ZoneInfo('Asia/Shanghai'); print('  自检OK', sys.version)" | Write-Host
if ($LASTEXITCODE -ne 0) { throw "Python自检失败" }

# ---------- 3. 数据源连通性 ----------
Write-Host "`n==== 数据源连通性 ====" -ForegroundColor Cyan
$env:PYTHONPATH = "$Root\scripts"
& $py "$Root\scripts\server_probe.py" 2>&1 | ForEach-Object { Write-Host "  $_" }

# ---------- 4. 定时任务 ----------
Write-Host "`n==== 定时任务 ====" -ForegroundColor Cyan
New-Item -ItemType Directory -Path "$Root\logs" -Force | Out-Null
$defs = @(
    @{ N = "kanban-web";  TR = "`"$py`" -m http.server $Port --directory $Root"; SC = "onstart"; RU = "SYSTEM" },
    @{ N = "kanban-hot";  TR = "`"$py`" `"$Root\scripts\hot_stocks.py`" >> `"$Root\logs\hot.log`" 2>&1"; SC = "minute"; MO = 10 },
    @{ N = "kanban-us";   TR = "`"$py`" `"$Root\scripts\us_sectors.py`" >> `"$Root\logs\us.log`" 2>&1"; SC = "minute"; MO = 10 },
    @{ N = "kanban-kr";   TR = "`"$py`" `"$Root\scripts\kr_stocks.py`" >> `"$Root\logs\kr.log`" 2>&1"; SC = "minute"; MO = 10 },
    @{ N = "kanban-cn1";  TR = "`"$py`" `"$Root\scripts\update_cn.py`" >> `"$Root\logs\cn.log`" 2>&1"; SC = "daily"; ST = "20:30" },
    @{ N = "kanban-cn2";  TR = "`"$py`" `"$Root\scripts\update_cn.py`" >> `"$Root\logs\cn.log`" 2>&1"; SC = "daily"; ST = "22:30" },
    @{ N = "kanban-cn3";  TR = "`"$py`" `"$Root\scripts\update_cn.py`" >> `"$Root\logs\cn.log`" 2>&1"; SC = "daily"; ST = "09:10" }
)
foreach ($t in $defs) {
    $args = @("/Create", "/F", "/TN", $t.N, "/TR", $t.TR, "/SC", $t.SC, "/RL", "HIGHEST")
    if ($t.MO) { $args += @("/MO", "$($t.MO)") }
    if ($t.ST) { $args += @("/ST", $t.ST) }
    if ($t.RU) { $args += @("/RU", $t.RU) }
    schtasks @args | Out-Null
    Write-Host "  [OK] $($t.N) ($($t.SC)$(if ($t.MO) { " /每$($t.MO)分" })$(if ($t.ST) { " @$($t.ST)" }))"
}
# 说明: hot/us/kr每10分钟全天运行, 脚本内置休市/时段自守卫; cn三个时点为收盘后主更新

# ---------- 5. 防火墙 + web ----------
netsh advfirewall firewall delete rule name = "kanban-web" 2>$null | Out-Null
netsh advfirewall firewall add rule name = "kanban-web" dir = in action = allow protocol = TCP localport = $Port | Out-Null
Write-Host "防火墙放行: TCP $Port"
schtasks /Run /TN "kanban-web" | Out-Null
Start-Sleep 4

# ---------- 6. 立即抓一轮数据 ----------
Write-Host "`n==== 首轮数据抓取 ====" -ForegroundColor Cyan
foreach ($s in @("hot_stocks.py", "us_sectors.py", "kr_stocks.py")) {
    & $py "$Root\scripts\$s" 2>&1 | ForEach-Object { Write-Host "  $_" }
}

# ---------- 7. 验证 ----------
$ip = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notmatch "^(127\.|169\.254)" } |
    Select-Object -First 1).IPAddress
Write-Host "`n==== 部署完成 ====" -ForegroundColor Green
Write-Host "访问地址: http://$ip`:$Port/  (外网)"
try {
    $r = Invoke-WebRequest "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 10
    Write-Host ("本机验证: HTTP {0} ({1}字节) -> 服务正常" -f $r.StatusCode, $r.RawContentStream.Length) -ForegroundColor Green
} catch {
    Write-Host "本机验证: web未响应 -> schtasks /Run /TN kanban-web 后重试" -ForegroundColor Red
}
Write-Host "查看任务: schtasks /Query /TN kanban-*   日志: $Root\logs\"
