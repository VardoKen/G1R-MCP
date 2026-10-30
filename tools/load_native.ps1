[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [string]$GameRoot = 'E:\SteamLibrary\steamapps\common\Gothic 1 Remake',
    [uint32]$ExpectedPid,
    [string]$ExpectedCreationTime,
    [string]$ExpectedDllSha256,
    [switch]$Json
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
. (Join-Path $PSScriptRoot 'game_process_common.ps1')
. (Join-Path $PSScriptRoot 'native_load_result.ps1')
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskLoader = Join-Path $taskRoot 'build\native\Release\g1r_mcp_loader.exe'
$taskDll = Join-Path $taskRoot 'build\native\Release\G1R_MCP.dll'
if (-not (Test-Path -LiteralPath $taskLoader -PathType Leaf) -or -not (Test-Path -LiteralPath $taskDll -PathType Leaf)) {
    throw 'Сначала выполните tools/build_native.ps1'
}
$lease = $null
$dllPin = $null
$loadStarted = $false
try {
    $expectedExe = (Resolve-Path -LiteralPath (Join-Path $GameRoot 'G1R\Binaries\Win64\G1R-Win64-Shipping.exe') -ErrorAction Stop).ProviderPath
    $controlled = $ExpectedPid -ne 0 -or -not [string]::IsNullOrWhiteSpace($ExpectedCreationTime) -or -not [string]::IsNullOrWhiteSpace($ExpectedDllSha256)
    if ($controlled) {
        if ($ExpectedPid -eq 0 -or [string]::IsNullOrWhiteSpace($ExpectedCreationTime) -or $ExpectedDllSha256 -cnotmatch '^[0-9a-fA-F]{64}$') {
            throw 'Для контролируемой загрузки нужны ExpectedPid, ExpectedCreationTime и ExpectedDllSha256 вместе.'
        }
    } else {
        $identities = @()
        foreach ($process in @(Get-Process -Name 'G1R-Win64-Shipping' -ErrorAction SilentlyContinue)) {
            $identity = Get-G1RProcessIdentity -ProcessId $process.Id
            if ($identity.executable -ieq $expectedExe) { $identities += $identity }
        }
        if ($identities.Count -ne 1) { throw 'Нет единственного игрового процесса из выбранного каталога игры.' }
        $ExpectedPid = $identities[0].pid
        $ExpectedCreationTime = $identities[0].creation_time
    }
    $lease = Open-G1RCheckedProcess -ExpectedPid $ExpectedPid -ExpectedCreationTime $ExpectedCreationTime -ExpectedExecutable $expectedExe
    $dllPin = [System.IO.File]::Open($taskDll, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try { $actualSha = ([BitConverter]::ToString($algorithm.ComputeHash($dllPin))).Replace('-', '').ToLowerInvariant() }
    finally { $algorithm.Dispose() }
    if ($controlled -and $actualSha -cne $ExpectedDllSha256.ToLowerInvariant()) { throw 'SHA-256 DLL изменился; загрузка отменена.' }
    if (-not $PSCmdlet.ShouldProcess("PID $ExpectedPid", 'Загрузить проверенную DLL через LoadLibraryW')) {
        $result = [pscustomobject]@{ state = 'not_started'; process = (ConvertTo-G1RProcessIdentity $lease.Identity); dll_sha256 = $actualSha; outcome_unknown = $false }
    } else {
        $creationFileTime = [DateTime]::ParseExact($ExpectedCreationTime, "yyyy-MM-ddTHH:mm:ss.fffffff'Z'", [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal).ToFileTimeUtc().ToString([Globalization.CultureInfo]::InvariantCulture)
        $loadStarted = $true
        $nativeOutput = & $taskLoader --pid $ExpectedPid --dll $taskDll --expected-exe $expectedExe --expected-creation-filetime $creationFileTime
        if ($LASTEXITCODE -ne 0) { throw 'Контролируемая загрузка DLL не подтверждена.' }
        $nativeResult = ConvertFrom-G1RNativeLoadResult -Source ($nativeOutput -join "`n") -ExpectedPid $ExpectedPid
        $result = [pscustomobject]@{ state = 'loaded'; process = (ConvertTo-G1RProcessIdentity $lease.Identity); dll_sha256 = $actualSha; native_result = $nativeResult; outcome_unknown = $false }
    }
    if ($Json) { $result | ConvertTo-Json -Depth 6 -Compress } else { $result }
} catch {
    if ($Json) {
        [pscustomobject]@{ state = 'failed'; message = 'Загрузка DLL в исходный процесс не подтверждена.'; outcome_unknown = $loadStarted } | ConvertTo-Json -Compress
        exit 2
    }
    throw
} finally {
    if ($null -ne $dllPin) { $dllPin.Dispose() }
    if ($null -ne $lease) { $lease.Dispose() }
}
