param(
    [string]$GameRoot = 'E:\SteamLibrary\steamapps\common\Gothic 1 Remake',
    [switch]$Json
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
. (Join-Path $PSScriptRoot 'game_process_common.ps1')
try {
    $resolvedRoot = (Resolve-Path -LiteralPath $GameRoot -ErrorAction Stop).ProviderPath
    $allowed = @((Join-Path $resolvedRoot 'G1R-Win64-Shipping.exe'), (Join-Path $resolvedRoot 'G1R\Binaries\Win64\G1R-Win64-Shipping.exe'))
    $processes = @()
    foreach ($candidate in @(Get-Process -Name 'G1R-Win64-Shipping' -ErrorAction SilentlyContinue)) {
        $identity = Get-G1RProcessIdentity -ProcessId $candidate.Id
        if ($allowed -contains $identity.executable) {
            $processes += $identity
        } else {
            throw 'Обнаружен одноимённый процесс вне выбранного каталога игры.'
        }
    }
    $result = [pscustomobject]@{ processes = @($processes); source = 'native_process_handle_snapshot' }
    if ($Json) { $result | ConvertTo-Json -Depth 5 -Compress } else { $result }
} catch {
    if ($Json) {
        [pscustomobject]@{ state = 'failed'; message = 'Не удалось однозначно проверить процессы выбранной игры.'; outcome_unknown = $false } | ConvertTo-Json -Compress
        exit 2
    }
    throw
}
