$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$recordPath = Join-Path $projectRoot '.monitor-services.json'
if (!(Test-Path -LiteralPath $recordPath)) { return }
$record = Get-Content -LiteralPath $recordPath | ConvertFrom-Json
$created = ([DateTime]$record.created_at).ToUniversalTime()
foreach ($name in @('monitor','tunnel','prometheus')) {
    if (!$record.$name) { continue }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($record.$name)"
    if (!$process) { continue }
    if ([Math]::Abs(($process.CreationDate.ToUniversalTime()-$created).TotalSeconds) -gt 60) { throw "监控进程 $name 时间不匹配，拒绝停止" }
    $expected = if ($name -eq 'prometheus') { Join-Path $projectRoot '.runtime\prometheus\bin\prometheus.exe' } else { Join-Path $projectRoot '.venv\Scripts\python.exe' }
    if (!$process.CommandLine.Contains($expected)) { throw "监控进程 $name 路径不匹配，拒绝停止" }
    if ($name -eq 'monitor' -and !$process.CommandLine.Contains('monitorworker')) { throw '监控同步器命令不匹配' }
    if ($name -eq 'tunnel' -and !$process.CommandLine.Contains('monitor-tunnel.py')) { throw '隧道命令不匹配' }
    Get-CimInstance Win32_Process -Filter "ParentProcessId=$($process.ProcessId)" | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }
    Stop-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
}
Write-Output '本地监控服务已停止，远端只读 Exporter 保持运行'
