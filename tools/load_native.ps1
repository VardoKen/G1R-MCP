[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskLoader = Join-Path $taskRoot 'build\native\Release\g1r_mcp_loader.exe'
$taskDll = Join-Path $taskRoot 'build\native\Release\G1R_MCP.dll'
if (-not (Test-Path -LiteralPath $taskLoader -PathType Leaf) -or -not (Test-Path -LiteralPath $taskDll -PathType Leaf)) {
    throw 'Сначала выполните tools/build_native.ps1'
}
$taskProcesses = @(Get-CimInstance Win32_Process -Filter "Name='G1R-Win64-Shipping.exe'" |
    Where-Object { $_.ExecutablePath -like '*\G1R\Binaries\Win64\G1R-Win64-Shipping.exe' })
if ($taskProcesses.Count -ne 1) { throw 'Нет единственного игрового процесса; запустите игру через Steam' }
& $taskLoader --pid $taskProcesses[0].ProcessId --dll $taskDll
if ($LASTEXITCODE -ne 0) { throw 'Нативный загрузчик завершился с ошибкой; повтор автоматически не выполняется' }
