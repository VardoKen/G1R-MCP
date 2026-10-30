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

function Get-G1RStopSnapshot {
    param([string]$Inner, [string]$Bootstrap)
    $items = @()
    foreach ($candidate in @(Get-CimInstance Win32_Process -Filter "Name='G1R-Win64-Shipping.exe'")) {
        $identity = Get-G1RProcessIdentity -ProcessId $candidate.ProcessId
        $fresh = @(Get-CimInstance Win32_Process -Filter ("ProcessId=" + $candidate.ProcessId))
        if ($fresh.Count -ne 1 -or $null -eq $candidate.CreationDate -or $null -eq $fresh[0].CreationDate) {
            throw 'Не удалось связать родительский PID с точной идентичностью процесса.'
        }
        $created = [DateTime]::ParseExact($identity.creation_time, "yyyy-MM-dd'T'HH:mm:ss.fffffff'Z'",
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::AssumeUniversal -bor [System.Globalization.DateTimeStyles]::AdjustToUniversal)
        if ($fresh[0].ProcessId -ne $candidate.ProcessId -or
            $fresh[0].ExecutablePath -ine $identity.executable -or
            $fresh[0].ParentProcessId -ne $candidate.ParentProcessId -or
            $fresh[0].CreationDate -ne $candidate.CreationDate -or
            [Math]::Abs(($fresh[0].CreationDate.ToUniversalTime() - $created).TotalMilliseconds) -gt 1) {
            throw 'Снимок родительского PID относится к изменившемуся процессу.'
        }
        if ($identity.executable -ine $Inner -and $identity.executable -ine $Bootstrap) {
            throw 'Обнаружен одноимённый процесс вне выбранного каталога игры.'
        }
        $items += [pscustomobject]@{ identity = $identity; parent_pid = [uint32]$fresh[0].ParentProcessId }
    }
    if (@($items | Where-Object { $_.identity.executable -ieq $Inner }).Count -gt 1 -or
        @($items | Where-Object { $_.identity.executable -ieq $Bootstrap }).Count -gt 1) {
        throw 'Обнаружено несколько экземпляров одного игрового EXE.'
    }
    return $items
}

function Test-G1RStopChild {
    param($Child, $ParentIdentity, [string]$ParentExitTime)
    if ($Child.parent_pid -ne $ParentIdentity.pid) { return $false }
    # Строки одной точной UTC-формы сравниваются в хронологическом порядке.
    if ([string]::CompareOrdinal($Child.identity.creation_time, $ParentIdentity.creation_time) -lt 0) { return $false }
    if (-not [string]::IsNullOrEmpty($ParentExitTime) -and
        [string]::CompareOrdinal($Child.identity.creation_time, $ParentExitTime) -gt 0) { return $false }
    return $true
}

$lease = $null
$bootstrapLease = $null
$innerLease = $null
$actionStarted = $false
$cleanup = @()
$selectedIdentity = $null
try {
    $resolvedRoot = (Resolve-Path -LiteralPath $GameRoot -ErrorAction Stop).ProviderPath
    $inner = (Resolve-Path -LiteralPath (Join-Path $resolvedRoot 'G1R\Binaries\Win64\G1R-Win64-Shipping.exe') -ErrorAction Stop).ProviderPath
    $bootstrap = (Resolve-Path -LiteralPath (Join-Path $resolvedRoot 'G1R-Win64-Shipping.exe') -ErrorAction Stop).ProviderPath
    $snapshot = @(Get-G1RStopSnapshot -Inner $inner -Bootstrap $bootstrap)
    $selected = @($snapshot | Where-Object {
        $_.identity.pid -eq $ExpectedPid -and $_.identity.creation_time -ceq $ExpectedCreationTime
    })
    if ($selected.Count -ne 1) { throw 'Идентичность ожидаемого игрового процесса изменилась.' }
    $selectedIdentity = $selected[0].identity
    $lease = Open-G1RCheckedProcess -ExpectedPid $ExpectedPid -ExpectedCreationTime $ExpectedCreationTime `
        -ExpectedExecutable $selectedIdentity.executable -ForTermination
    $selectedIsBootstrap = $selectedIdentity.executable -ieq $bootstrap
    if ($selectedIsBootstrap) {
        $bootstrapLease = $lease
        $candidateInner = @($snapshot | Where-Object { $_.identity.executable -ieq $inner })
        if ($candidateInner.Count -eq 1) {
            if (-not (Test-G1RStopChild -Child $candidateInner[0] -ParentIdentity $selectedIdentity)) {
                throw 'Внутренний процесс не принадлежит выбранному загрузчику.'
            }
            $identity = $candidateInner[0].identity
            $innerLease = Open-G1RCheckedProcess -ExpectedPid $identity.pid -ExpectedCreationTime $identity.creation_time `
                -ExpectedExecutable $inner -ForTermination
        }
    } else {
        $innerLease = $lease
        $candidateBootstrap = @($snapshot | Where-Object { $_.identity.executable -ieq $bootstrap })
        if ($candidateBootstrap.Count -eq 1) {
            if (-not (Test-G1RStopChild -Child $selected[0] -ParentIdentity $candidateBootstrap[0].identity)) {
                throw 'Загрузчик не является родителем выбранного внутреннего процесса.'
            }
            $identity = $candidateBootstrap[0].identity
            $bootstrapLease = Open-G1RCheckedProcess -ExpectedPid $identity.pid -ExpectedCreationTime $identity.creation_time `
                -ExpectedExecutable $bootstrap -ForTermination
        }
    }
    if (-not $PSCmdlet.ShouldProcess("PID $ExpectedPid", 'Завершить точный процесс выбранной игровой сессии')) {
        $result = [pscustomobject]@{ state = 'not_started'; process = $selectedIdentity; cleanup = @(); outcome_unknown = $false }
    } else {
        $actionStarted = $true
        # Сначала прекращаем исходный загрузчик: после сигнала HANDLE он не создаст новых детей.
        if ($null -ne $bootstrapLease) {
            $cleanup += Stop-G1RCheckedProcess -Lease $bootstrapLease -GraceMilliseconds 1000 -ForceWaitMilliseconds 3000 -AllowForce
        }
        if ($selectedIsBootstrap) {
            $exitTime = $bootstrapLease.GetExitTime()
            $parentIdentity = ConvertTo-G1RProcessIdentity $bootstrapLease.Identity
            # Поздний ребёнок допустим только в подтверждённой жизни удерживаемого parent.
            $deadline = [System.Diagnostics.Stopwatch]::StartNew()
            do {
                $lateSnapshot = @(Get-G1RStopSnapshot -Inner $inner -Bootstrap $bootstrap)
                $lateInner = @($lateSnapshot | Where-Object { $_.identity.executable -ieq $inner })
                $lateBootstrap = @($lateSnapshot | Where-Object { $_.identity.executable -ieq $bootstrap })
                if ($lateBootstrap.Count -gt 0) { throw 'После остановки появился другой загрузчик; дальнейшая очистка отменена.' }
                if ($lateInner.Count -eq 1) {
                    if (-not (Test-G1RStopChild -Child $lateInner[0] -ParentIdentity $parentIdentity -ParentExitTime $exitTime)) {
                        throw 'Поздний внутренний процесс не относится к времени жизни исходного загрузчика.'
                    }
                    $identity = $lateInner[0].identity
                    if ($null -eq $innerLease) {
                        $innerLease = Open-G1RCheckedProcess -ExpectedPid $identity.pid -ExpectedCreationTime $identity.creation_time `
                            -ExpectedExecutable $inner -ForTermination
                    } elseif ($innerLease.Identity.Pid -ne $identity.pid -or
                        $innerLease.Identity.CreationTime -cne $identity.creation_time) {
                        throw 'Внутренний процесс заменён; дальнейшая очистка отменена.'
                    }
                    break
                }
                if ($null -ne $innerLease -or $deadline.ElapsedMilliseconds -ge 750) { break }
                Start-Sleep -Milliseconds 50
            } while ($true)
        }
        if ($null -ne $innerLease) {
            $cleanup += Stop-G1RCheckedProcess -Lease $innerLease -GraceMilliseconds 15000 -ForceWaitMilliseconds 3000 -AllowForce
        }
        $remaining = @(Get-G1RStopSnapshot -Inner $inner -Bootstrap $bootstrap)
        if ($remaining.Count -ne 0) { throw 'После завершения исходных HANDLE обнаружены игровые процессы; очистка неполная.' }
        $mode = if ($cleanup.Count -gt 0) { $cleanup[0].mode } else { 'none' }
        $result = [pscustomobject]@{ state = 'stopped'; mode = $mode; process = $selectedIdentity;
            cleanup = @($cleanup); outcome_unknown = $false }
    }
    if ($Json) { $result | ConvertTo-Json -Depth 7 -Compress } else { $result }
} catch {
    if ($Json) {
        [pscustomobject]@{ state = 'failed'; code = 'session_cleanup_incomplete';
            message = 'Остановка исходной игровой сессии не подтверждена.';
            process = $selectedIdentity; cleanup = @($cleanup); outcome_unknown = $actionStarted } | ConvertTo-Json -Depth 7 -Compress
        exit 2
    }
    throw
} finally {
    if ($null -ne $innerLease -and $innerLease -ne $lease) { $innerLease.Dispose() }
    if ($null -ne $bootstrapLease -and $bootstrapLease -ne $lease) { $bootstrapLease.Dispose() }
    if ($null -ne $lease) { $lease.Dispose() }
}
