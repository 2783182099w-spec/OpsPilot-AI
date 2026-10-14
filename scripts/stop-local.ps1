$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$recordPath = Join-Path $projectRoot '.local-services.json'
if (!(Test-Path -LiteralPath $recordPath)) { throw '没有本地服务进程记录' }
$record = Get-Content -LiteralPath $recordPath | ConvertFrom-Json
$recordTime = (Get-Item -LiteralPath $recordPath).LastWriteTimeUtc
foreach ($serviceName in @('api', 'worker', 'web')) {
    $serviceProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($record.$serviceName)"
    if (!$serviceProcess) { continue }
    if ([Math]::Abs(($serviceProcess.CreationDate.ToUniversalTime() - $recordTime).TotalSeconds) -gt 60) { throw "进程 $serviceName 时间不匹配，拒绝停止" }
    $expected = if ($serviceName -eq 'web') { 'node_modules/@vue/cli-service/bin/vue-cli-service.js' } else { (Join-Path $projectRoot '.venv\Scripts\python.exe') }
    if (!$serviceProcess.CommandLine.Contains($expected)) { throw "进程 $serviceName 命令不匹配，拒绝停止" }
    Get-CimInstance Win32_Process -Filter "ParentProcessId=$($serviceProcess.ProcessId)" | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }
    Stop-Process -Id $serviceProcess.ProcessId -ErrorAction SilentlyContinue
}
Write-Output '本项目的本地开发服务已停止'
