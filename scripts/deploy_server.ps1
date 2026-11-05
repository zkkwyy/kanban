# kanban - Windows server one-click deploy
# Usage: admin PowerShell:
#   powershell -ExecutionPolicy Bypass -File deploy_server.ps1 [-Port 8080]
param(
    [int]$Port = 8080,
    [string]$Root = "C:\kanban"
)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Write-Host "==== kanban deploy ====" -ForegroundColor Cyan

# ---------- 1. site code ----------
$zip = "$env:TEMP\kanban-main.zip"
$srcUrls = @(
    "https://codeload.github.com/zkkwyy/kanban/zip/refs/heads/main",
    "https://ghproxy.net/https://github.com/zkkwyy/kanban/archive/refs/heads/main.zip",
    "https://gh-proxy.com/https://github.com/zkkwyy/kanban/archive/refs/heads/main.zip"
)
function Get-Zip($Urls) {
    foreach ($u in $Urls) {
        try {
            Write-Host "download: $u"
            Invoke-WebRequest $u -OutFile $zip -UseBasicParsing -TimeoutSec 120
            if ((Get-Item $zip).Length -gt 100KB) { return $true }
        } catch { Write-Host "  fail: $($_.Exception.Message)" -ForegroundColor Yellow }
    }
    return $false
}
$ok = Get-Zip $srcUrls
if (-not $ok) {
    Write-Host "retry with cert check bypass..." -ForegroundColor Yellow
    [Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
    $ok = Get-Zip $srcUrls
    [Net.ServicePointManager]::ServerCertificateValidationCallback = $null
}
if (-not $ok) { throw "download failed: need GitHub or mirror access" }
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
Write-Host "site ready: $Root"

# ---------- 2. portable python ----------
$py = "$Root\python\python.exe"
if (-not (Test-Path $py)) {
    $pyzip = "$env:TEMP\python-embed.zip"
    try {
        Invoke-WebRequest "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip" `
            -OutFile $pyzip -UseBasicParsing -TimeoutSec 300
    } catch { throw "python download failed: $($_.Exception.Message)" }
    Expand-Archive $pyzip -DestinationPath "$Root\python" -Force
}
$pth = "$Root\python\python311._pth"
if (Test-Path $pth) {
    (Get-Content $pth) -replace "^#\s*import\s+site", "import site" | Set-Content $pth
}
Write-Host "python: $py"
& $py -c "import zoneinfo,sys; sys.path.insert(0,r'$Root\scripts'); zoneinfo.ZoneInfo('Asia/Shanghai'); print('  selftest OK')" | Write-Host
if ($LASTEXITCODE -ne 0) { throw "python selftest failed" }

# ---------- 3. connectivity probe ----------
Write-Host "`n==== data source connectivity ====" -ForegroundColor Cyan
& $py "$Root\scripts\server_probe.py" 2>&1 | ForEach-Object { Write-Host "  $_" }

# ---------- 4. task runner (batch, avoids schtasks quoting hell) ----------
New-Item -ItemType Directory -Path "$Root\logs" -Force | Out-Null
$cmd = @"
@echo off
cd /d $Root
"$py" "scripts\%~1" >> "logs\%~n1.log" 2>&1
"@
[System.IO.File]::WriteAllText("$Root\run_task.cmd", $cmd, [System.Text.Encoding]::ASCII)

# ---------- 5. scheduled tasks ----------
Write-Host "`n==== scheduled tasks ====" -ForegroundColor Cyan
$defs = @(
    @{ N = "kanban-web";  S = "hot_stocks.py";   SC = "onstart"; RU = "SYSTEM"; WEB = $true },
    @{ N = "kanban-hot";  S = "hot_stocks.py";   SC = "minute"; MO = 10 },
    @{ N = "kanban-us";   S = "us_sectors.py";   SC = "minute"; MO = 10 },
    @{ N = "kanban-kr";   S = "kr_stocks.py";    SC = "minute"; MO = 10 },
    @{ N = "kanban-cn1";  S = "update_cn.py";    SC = "daily";  ST = "20:30" },
    @{ N = "kanban-cn2";  S = "update_cn.py";    SC = "daily";  ST = "22:30" },
    @{ N = "kanban-cn3";  S = "update_cn.py";    SC = "daily";  ST = "09:10" }
)
foreach ($t in $defs) {
    if ($t.WEB) {
        $tr = "`"$py`" -m http.server $Port --directory $Root"
    } else {
        $tr = "`"$Root\run_task.cmd`" $($t.S)"
    }
    $sa = @("/Create", "/F", "/TN", $t.N, "/TR", $tr, "/SC", $t.SC, "/RL", "HIGHEST")
    if ($t.MO) { $sa += @("/MO", "$($t.MO)") }
    if ($t.ST) { $sa += @("/ST", $t.ST) }
    if ($t.RU) { $sa += @("/RU", $t.RU) }
    schtasks @sa | Out-Null
    $desc = "$($t.SC)$(if ($t.MO) { ' every-' + $t.MO + 'min' })$(if ($t.ST) { ' @' + $t.ST })"
    Write-Host ("  [OK] {0,-12} {1}" -f $t.N, $desc)
}
# hot/us/kr run 24h; scripts self-guard (market-closed skip / failure keeps old data)

# ---------- 6. firewall + web ----------
netsh advfirewall firewall delete rule name = "kanban-web" 2>$null | Out-Null
netsh advfirewall firewall add rule name = "kanban-web" dir = in action = allow protocol = TCP localport = $Port | Out-Null
Write-Host "firewall: TCP $Port allowed"
schtasks /Run /TN "kanban-web" | Out-Null
Start-Sleep 4

# ---------- 7. first data run ----------
Write-Host "`n==== first data run ====" -ForegroundColor Cyan
foreach ($s in @("hot_stocks.py", "us_sectors.py", "kr_stocks.py")) {
    & $py "$Root\scripts\$s" 2>&1 | ForEach-Object { Write-Host "  $_" }
}

# ---------- 8. verify ----------
$ip = (Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notmatch "^(127\.|169\.254)" } |
    Select-Object -First 1).IPAddress
Write-Host "`n==== DEPLOY DONE ====" -ForegroundColor Green
Write-Host "URL: http://$ip`:$Port/  (public)"
try {
    $r = Invoke-WebRequest "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 10
    Write-Host ("local check: HTTP {0} ({1} bytes) -> OK" -f $r.StatusCode, $r.RawContentStream.Length) -ForegroundColor Green
} catch {
    Write-Host "local check: web not responding -> try: schtasks /Run /TN kanban-web" -ForegroundColor Red
}
Write-Host "tasks: schtasks /Query /TN kanban-*    logs: $Root\logs\"
