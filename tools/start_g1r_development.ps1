<#
.SYNOPSIS
Запускает Gothic 1 Remake в режиме разработки через Steam.

.DESCRIPTION
Сохраняет наблюдавшийся маршрут -FromModManagementUI и добавляет разовые
параметры AngelScript. Не изменяет параметры Steam, Mods.ini, кеш или файлы
игры. Командная строка не подтверждает фактическую активацию модов.

Без -Restart работающая игра вызывает отказ. -Restart штатно закрывает
только проверенный игровой процесс и ожидает завершения загрузчика;
без ExpectedPid и ExpectedCreationTime принудительное завершение не используется.
С точной идентичностью контролируемый restart использует удерживаемый HANDLE
и может завершить исходную сессию после ограниченного ожидания. -WhatIf не закрывает игру
и не отправляет запрос запуска в Steam.

.EXAMPLE
.\tools\start_g1r_development.ps1 -Restart -WhatIf

.EXAMPLE
.\tools\start_g1r_development.ps1 -Restart
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [string]$SteamExe,
    [string]$GameRoot = 'E:\SteamLibrary\steamapps\common\Gothic 1 Remake',
    [string]$AppId = '1297900',
    [string]$ModsDirectory,
    [string[]]$Mods,
    [string]$ModsCsv,
    [string]$ScriptDirectory,
    [switch]$ShowScriptErrors,

    [switch]$EnableIngameTools,
    [uint32]$ExpectedPid,
    [string]$ExpectedCreationTime,
    [switch]$Json,
    [switch]$Restart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
. (Join-Path $PSScriptRoot 'game_process_common.ps1')

function Get-RequiredPath {
    param([string]$Path, [string]$Label, [switch]$Directory)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "$Label`: путь не задан."
    }
    try {
        $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    }
    catch {
        throw "$Label`: путь не найден или недоступен."
    }
    if ($item.PSProvider.Name -ne 'FileSystem' -or
        $item.PSIsContainer -ne [bool]$Directory) {
        throw "$Label`: неподходящий тип пути."
    }
    return [System.IO.Path]::GetFullPath($item.FullName)
}

function Get-CheckedGameProcesses {
    param([string]$BootstrapPath, [string]$InnerPath)

    $processes = @(Get-CimInstance Win32_Process -Filter "Name='G1R-Win64-Shipping.exe'")
    foreach ($process in $processes) {
        if ([string]::IsNullOrWhiteSpace($process.ExecutablePath) -or
            ($process.ExecutablePath -ine $BootstrapPath -and
             $process.ExecutablePath -ine $InnerPath)) {
            throw 'Обнаружен игровой процесс с неподтверждённым путём; запуск и закрытие отменены.'
        }
    }
    foreach ($allowedPath in @($BootstrapPath, $InnerPath)) {
        if (@($processes | Where-Object { $_.ExecutablePath -ieq $allowedPath }).Count -gt 1) {
            throw 'Обнаружено несколько экземпляров игры или загрузчика; запуск и закрытие отменены.'
        }
    }
    return $processes
}

function Assert-NoPathRedirect {
    param([string]$Path)
    $current = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    while ($null -ne $current) {
        if (($current.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'Путь пакета содержит ссылку или junction; запуск отменён.'
        }
        if ($current -is [System.IO.DirectoryInfo]) {
            $current = $current.Parent
        } else {
            $current = $current.Directory
        }
    }
}

if ($AppId -cne '1297900') {
    throw 'Этот инструмент поддерживает только Steam AppID 1297900.'
}

$resolvedGameRoot = Get-RequiredPath -Path $GameRoot -Label 'Каталог игры' -Directory
$bootstrapPath = Get-RequiredPath -Path (Join-Path $resolvedGameRoot 'G1R-Win64-Shipping.exe') -Label 'Загрузчик игры'
$innerPath = Get-RequiredPath -Path (Join-Path $resolvedGameRoot 'G1R\Binaries\Win64\G1R-Win64-Shipping.exe') -Label 'Игровой EXE'

if ([string]::IsNullOrWhiteSpace($SteamExe)) {
    try {
        $steamSettings = Get-ItemProperty -LiteralPath 'HKCU:\Software\Valve\Steam'
        $steamRoot = [string]$steamSettings.SteamPath
    }
    catch {
        throw 'Путь Steam не найден в реестре; задайте -SteamExe явно.'
    }
    if ([string]::IsNullOrWhiteSpace($steamRoot)) {
        throw 'Путь Steam не найден в реестре; задайте -SteamExe явно.'
    }
    $SteamExe = Join-Path $steamRoot 'steam.exe'
}
$resolvedSteamExe = Get-RequiredPath -Path $SteamExe -Label 'Steam EXE'
if ([System.IO.Path]::GetFileName($resolvedSteamExe) -ine 'steam.exe') {
    throw 'Для запуска нужен файл steam.exe.'
}

$packageOptions = @()
$scriptOptions = @()
if (-not [string]::IsNullOrWhiteSpace($ScriptDirectory)) {
    if ($ScriptDirectory -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\[^\\]+)' -or $ScriptDirectory -match '["\x00-\x1f]' -or $ScriptDirectory.Substring(2).Contains(':')) {
        throw 'ScriptDirectory должен быть абсолютным путём без кавычек и управляющих символов.'
    }
    $resolvedScriptDirectory = Get-RequiredPath -Path $ScriptDirectory -Label 'Основная Script-база' -Directory
    Assert-NoPathRedirect -Path $resolvedScriptDirectory
    $scriptOptions = @(('-ScriptDirectory="' + $resolvedScriptDirectory.Replace('\', '/') + '"'))
}
$resolvedModsDirectory = $null
$selectedMods = @($Mods | Where-Object { $null -ne $_ })
if ($PSBoundParameters.ContainsKey('ModsCsv')) {
    if ($PSBoundParameters.ContainsKey('Mods')) {
        throw 'ModsCsv и Mods нельзя указывать вместе.'
    }
    if ([string]::IsNullOrWhiteSpace($ModsCsv) -or $ModsCsv.Length -gt 515) {
        throw 'ModsCsv должен содержать от 1 до 4 имён пакетов через запятую.'
    }
    $selectedMods = @($ModsCsv.Split([char]','))
    if ($selectedMods.Count -lt 1 -or $selectedMods.Count -gt 4) {
        throw 'ModsCsv должен содержать от 1 до 4 имён пакетов через запятую.'
    }
    foreach ($mod in $selectedMods) {
        if ($mod -cnotmatch '\A[A-Za-z0-9_-]{1,128}\z') {
            throw 'ModsCsv содержит недопустимое имя пакета.'
        }
    }
}
if (-not [string]::IsNullOrWhiteSpace($ModsDirectory) -or $selectedMods.Count -gt 0) {
    if ([string]::IsNullOrWhiteSpace($ModsDirectory) -or $selectedMods.Count -eq 0) {
        throw 'ModsDirectory и Mods должны быть указаны вместе.'
    }
    if ($ModsDirectory -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\[^\\]+)' -or $ModsDirectory -match '["\x00-\x1f]' -or $ModsDirectory.Substring(2).Contains(':')) {
        throw 'ModsDirectory должен быть абсолютным путём без кавычек и управляющих символов.'
    }
    $resolvedModsDirectory = Get-RequiredPath -Path $ModsDirectory -Label 'Каталог пакетов' -Directory
    Assert-NoPathRedirect -Path $resolvedModsDirectory
    $uniqueMods = @{}
    foreach ($mod in $selectedMods) {
        if ([string]::IsNullOrWhiteSpace($mod) -or $mod -cnotmatch '^[A-Za-z0-9_-]{1,128}$' -or $uniqueMods.ContainsKey($mod)) {
            throw 'Имена Mods должны быть уникальными именами каталогов из букв, цифр, _ или -.'
        }
        $uniqueMods[$mod] = $true
        $packagePath = Get-RequiredPath -Path (Join-Path $resolvedModsDirectory $mod) -Label 'Пакет' -Directory
        Assert-NoPathRedirect -Path $packagePath
        $descriptorPath = Get-RequiredPath -Path (Join-Path $packagePath 'GothicMod.json') -Label 'Дескриптор пакета'
        Assert-NoPathRedirect -Path $descriptorPath
        $descriptor = Get-Content -LiteralPath $descriptorPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        if ($null -eq $descriptor -or $null -eq $descriptor.PSObject.Properties['name'] -or $descriptor.name -cne $mod) {
            throw 'Имя в GothicMod.json должно совпадать с выбранным каталогом Mods.'
        }
    }
    $quotedModsDirectory = $resolvedModsDirectory.Replace('\', '/')
    $packageOptions = @(('-ModsDirectory="' + $quotedModsDirectory + '"'), ('-Mods="' + ($selectedMods -join ',') + '"'))
}

$runningProcesses = @(Get-CheckedGameProcesses -BootstrapPath $bootstrapPath -InnerPath $innerPath)
if ($runningProcesses.Count -gt 0 -and -not $Restart) {
    throw 'Игра уже запущена. Для штатного перезапуска используйте -Restart.'
}

$steamArguments = @(
    '-applaunch', $AppId,
    '-FromModManagementUI',
    '-as-development-mode',
    '-as-ignore-precompiled-data'
)
if (-not $ShowScriptErrors) { $steamArguments += '-as-exit-on-error' }
if ($EnableIngameTools) { $steamArguments += '-ingametools' }
$steamArguments += $packageOptions + $scriptOptions
$operation = if ($runningProcesses.Count -gt 0) {
    'Штатно закрыть игру и запустить режим разработки через Steam'
}
else {
    'Запустить режим разработки через Steam'
}

if (-not $PSCmdlet.ShouldProcess($resolvedGameRoot, $operation)) {
    $result = [pscustomobject]@{
        State = 'Без изменения состояния'
        package_name = $(if ($selectedMods.Count -eq 1) { $selectedMods[0] } else { $null })
        mods = @($selectedMods)
        mods_directory = $resolvedModsDirectory
        SteamExe = $resolvedSteamExe
        AppId = $AppId
        Arguments = $steamArguments
        RestartRequested = [bool]$Restart
    }
    if ($Json) { $result | ConvertTo-Json -Depth 5 -Compress } else { $result }
    return
}

# Перепроверить состояние после возможного ожидания подтверждения.
$runningProcesses = @(Get-CheckedGameProcesses -BootstrapPath $bootstrapPath -InnerPath $innerPath)
if ($runningProcesses.Count -gt 0 -and -not $Restart) {
    throw 'Игра запущена после начальной проверки; повторный запуск отменён.'
}

if ($runningProcesses.Count -gt 0 -and ($ExpectedPid -ne 0 -or -not [string]::IsNullOrWhiteSpace($ExpectedCreationTime))) {
    if (-not $Restart -or $ExpectedPid -eq 0 -or [string]::IsNullOrWhiteSpace($ExpectedCreationTime)) {
        throw 'Контролируемый restart требует Restart, ExpectedPid и ExpectedCreationTime вместе.'
    }
    $stoppedJson = & (Join-Path $PSScriptRoot 'stop_g1r_development.ps1') -GameRoot $resolvedGameRoot -ExpectedPid $ExpectedPid -ExpectedCreationTime $ExpectedCreationTime -Json -Confirm:$false
    $stopped = $stoppedJson | ConvertFrom-Json -ErrorAction Stop
    if ($stopped.state -ne 'stopped') { throw 'Завершение исходной сессии перед запуском не подтверждено.' }
    $runningProcesses = @(Get-CheckedGameProcesses -BootstrapPath $bootstrapPath -InnerPath $innerPath)
} elseif ($runningProcesses.Count -eq 0 -and ($ExpectedPid -ne 0 -or -not [string]::IsNullOrWhiteSpace($ExpectedCreationTime))) {
    throw 'Ожидаемая игровая сессия уже завершилась; новый запуск отменён.'
}
if ($runningProcesses.Count -gt 0) {
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    $originalProcessIds = @($runningProcesses | ForEach-Object { $_.ProcessId })
    $innerProcesses = @($runningProcesses | Where-Object { $_.ExecutablePath -ieq $innerPath })
    foreach ($process in $innerProcesses) {
        $handle = Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
        if ($null -eq $handle) {
            continue
        }
        $fresh = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $process.ProcessId)
        if ($null -eq $fresh -or $fresh.ExecutablePath -ine $innerPath -or
            $fresh.CreationDate -ne $process.CreationDate -or
            [Math]::Abs(($handle.StartTime.ToUniversalTime() - $process.CreationDate.ToUniversalTime()).TotalMilliseconds) -gt 1) {
            throw 'Идентичность игрового процесса изменилась; закрытие и запуск отменены.'
        }
        if (-not $handle.CloseMainWindow()) {
            throw 'Игра не приняла штатное закрытие окна; принудительное завершение не выполняется.'
        }
    }

    # Загрузчик обычно завершается вслед за игровым процессом. Если игра ещё
    # не появилась, загрузчику можно отправить только штатное закрытие окна.
    if ($innerProcesses.Count -eq 0) {
        foreach ($process in $runningProcesses) {
            $handle = Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
            if ($null -ne $handle) {
                $fresh = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $process.ProcessId)
                if ($null -eq $fresh -or $fresh.ExecutablePath -ine $bootstrapPath -or
                    $fresh.CreationDate -ne $process.CreationDate -or
                    [Math]::Abs(($handle.StartTime.ToUniversalTime() - $process.CreationDate.ToUniversalTime()).TotalMilliseconds) -gt 1) {
                    throw 'Идентичность загрузчика изменилась; закрытие и запуск отменены.'
                }
                if (-not $handle.CloseMainWindow()) {
                    throw 'Загрузчик не принял штатное закрытие; запуск отменён.'
                }
            }
        }
    }

    do {
        $remaining = @(Get-CheckedGameProcesses -BootstrapPath $bootstrapPath -InnerPath $innerPath)
        if (@($remaining | Where-Object { $_.ProcessId -notin $originalProcessIds }).Count -gt 0) {
            throw 'Во время завершения появился новый игровой процесс; новый запуск отменён.'
        }
        if ($remaining.Count -eq 0) {
            break
        }
        $milliseconds = [Math]::Min(200, [Math]::Max(0, ($deadline - [DateTime]::UtcNow).TotalMilliseconds))
        if ($milliseconds -gt 0) {
            Start-Sleep -Milliseconds ([int]$milliseconds)
        }
    } while ([DateTime]::UtcNow -lt $deadline)

    if (@(Get-CheckedGameProcesses -BootstrapPath $bootstrapPath -InnerPath $innerPath).Count -gt 0) {
        throw 'Игра или загрузчик не завершились за 20 секунд; новый запуск отменён.'
    }
}

if (@(Get-CheckedGameProcesses -BootstrapPath $bootstrapPath -InnerPath $innerPath).Count -gt 0) {
    throw 'Игра появилась перед запуском Steam; повторный запуск отменён.'
}
$request = Start-Process -FilePath $resolvedSteamExe -ArgumentList $steamArguments -WindowStyle Hidden -PassThru
$result = [pscustomobject]@{
    state = 'launch_requested'
    package_name = $(if ($selectedMods.Count -eq 1) { $selectedMods[0] } else { $null })
    mods = @($selectedMods)
    mods_directory = $resolvedModsDirectory
    description = 'Запрос запуска отправлен в Steam'
    SteamExe = $resolvedSteamExe
    AppId = $AppId
    Arguments = $steamArguments
    SteamRequestPid = $request.Id
    Note = 'PID относится к запросу Steam; загрузка игры и активация модов ещё требуют проверки.'
}
if ($Json) { $result | ConvertTo-Json -Depth 5 -Compress } else { $result }
