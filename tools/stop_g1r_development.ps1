[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [string]$GameRoot = 'E:\SteamLibrary\steamapps\common\Gothic 1 Remake',
    [Parameter(Mandatory = $true)][uint32]$ExpectedPid,
    [Parameter(Mandatory = $true)][string]$ExpectedCreationTime,
    [switch]$Json
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
. (Join-Path $PSScriptRoot 'game_process_common.ps1')
$lease = $null
$bootstrapLease = $null
$actionStarted = $false
try {
    $resolvedRoot = (Resolve-Path -LiteralPath $GameRoot -ErrorAction Stop).ProviderPath
    $inner = (Resolve-Path -LiteralPath (Join-Path $resolvedRoot 'G1R\Binaries\Win64\G1R-Win64-Shipping.exe') -ErrorAction Stop).ProviderPath
    $bootstrap = (Resolve-Path -LiteralPath (Join-Path $resolvedRoot 'G1R-Win64-Shipping.exe') -ErrorAction Stop).ProviderPath
    $lease = Open-G1RCheckedProcess -ExpectedPid $ExpectedPid -ExpectedCreationTime $ExpectedCreationTime -ExpectedExecutable $inner -ForTermination
    # Снимок bootstrap берётся до действий и удерживается своим HANDLE.
    foreach ($candidate in @(Get-Process -Name 'G1R-Win64-Shipping' -ErrorAction SilentlyContinue)) {
        if ($candidate.Id -eq $ExpectedPid) { continue }
        $identity = Get-G1RProcessIdentity -ProcessId $candidate.Id
        if ($identity.executable -ine $bootstrap -or $null -ne $bootstrapLease) {
            throw 'Обнаружен дополнительный процесс, который нельзя однозначно связать с выбранной игрой.'
        }
        $bootstrapLease = Open-G1RCheckedProcess -ExpectedPid $identity.pid -ExpectedCreationTime $identity.creation_time -ExpectedExecutable $bootstrap -ForTermination
    }
    if (-not $PSCmdlet.ShouldProcess("PID $ExpectedPid", 'Завершить точный процесс выбранной игровой сессии')) {
        $result = [pscustomobject]@{ state = 'not_started'; process = (ConvertTo-G1RProcessIdentity $lease.Identity); outcome_unknown = $false }
    } else {
        $actionStarted = $true
        $result = Stop-G1RCheckedProcess -Lease $lease -GraceMilliseconds 15000 -ForceWaitMilliseconds 3000 -AllowForce
        if ($null -ne $bootstrapLease -and $bootstrapLease.IsRunning()) {
            $null = Stop-G1RCheckedProcess -Lease $bootstrapLease -GraceMilliseconds 1000 -ForceWaitMilliseconds 3000 -AllowForce
        }
        # already_exited подтверждает завершение того же удерживаемого процесса.
        $result.state = 'stopped'
    }
    if ($Json) { $result | ConvertTo-Json -Depth 5 -Compress } else { $result }
} catch {
    if ($Json) {
        [pscustomobject]@{ state = 'failed'; message = 'Остановка исходной игровой сессии не подтверждена.'; outcome_unknown = $actionStarted } | ConvertTo-Json -Compress
        exit 2
    }
    throw
} finally {
    if ($null -ne $bootstrapLease) { $bootstrapLease.Dispose() }
    if ($null -ne $lease) { $lease.Dispose() }
}
