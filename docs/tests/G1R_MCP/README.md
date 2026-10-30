# Проверки G1R MCP: диагностика и локальный сервер

Дата: **30.10.2026**. Текущая версия сервера **1.0.0-dev.1**, официальный SDK **mcp 2.3.0**, проверено Python **3.14.6**. Игрового мода и канала ещё нет; набор не является приёмкой полного выпуска 1.0.0.

## Автономный набор

Из корня подпроекта:

```powershell
uv sync --locked
.venv\Scripts\python.exe -B -m unittest discover -s tests -q
```

**52 теста прошли**: 12 для среды, 15 для диагностического протокола, 12 для источников, 12 для пакетов и одна сквозная MCP stdio-сессия с несколькими сценариями.

- [Среда](../../../tests/test_game_environment.py): allowlist Steam-метаданных, повреждённый ACF, несовпадающий каталог, отсутствующие/различающиеся скрипты, предел списков, symlink и отсутствие изменений файлов.
- [Протокол](../../../tests/test_angelscript_debug_protocol.py): асимметрия направлений, фрагментация и несколько кадров, ограничение/усечение, UTF-8/UTF-16, однократные запросы и отсутствие повтора после timeout. Socketpair заменяет игру.
- [Источники](../../../tests/test_sources.py): разрешённые корни, реальные строки, лексические типы, partial scan, ограничения Unicode-ответа, повреждённый/изменённый файл и выход за корни.
- [Пакеты](../../../tests/test_packages.py): официальный дескриптор, зависимости, content-only пакет, duplicate/NaN/Infinity, неизвестные поля и отсутствие записи.
- [MCP-сессия](../../../tests/test_mcp_server.py): дочерний stdio-сервер, initialize/list tools, все семь вызовов, structured content, ошибки пути/лимита и сохранение входных файлов.

## Read-only сверка установленной среды

```powershell
python -B tools/inspect_game_environment.py `
  --game-root 'E:\SteamLibrary\steamapps\common\Gothic 1 Remake' `
  --modkit-root 'C:\Users\Vardo\Documents\G1R Mods\G1R-Modkit' `
  --steam-manifest 'E:\SteamLibrary\steamapps\appmanifest_1297900.acf' `
  --hash-executable
```

Инструмент читает только явно заданные источники и выдаёт JSON. Он не запускает игру, не читает сохранения и не изменяет файлы. Совпадение скриптов не подтверждает работоспособность конкретного API.

## Запуск development mode через Steam

[start_g1r_development.ps1](../../../tools/start_g1r_development.ps1) сохраняет наблюдавшийся `-FromModManagementUI`, добавляет разовые согласованные параметры и использует Steam AppID `1297900`. Проверен синтаксис и `-WhatIf` в PowerShell 7 / Windows PowerShell 5.1; файл UTF-8 с BOM для совместимости PS5.1.

```powershell
.\tools\start_g1r_development.ps1 -Restart -WhatIf
```

Без `-WhatIf` скрипт запускает игру; `-Restart` штатно закрывает проверенный текущий процесс. Команда используется только в рамках существующего прямого разрешения запуска. Список модов, Steam LaunchOptions, cache и конфигурация не изменяются; фактическая активация модов по одной командной строке не доказывается.

## Разовая проба debugger

```powershell
python -B tools/probe_angelscript_debugger.py --timeout 3
```

По умолчанию одно подключение, только получение handshake и `Disconnect` при установленном соединении. `--request-database` однократно запрашивает базу, сохраняя лишь метаданные размера/количества. `--start-debugging` и `--evaluate` включаются явно; Evaluate допускает только ASCII-литерал и явный `frame-id`. Они не выполнялись в игре: подключение не установлено.

Формат закреплён по открытому клиенту Hazelight ревизии `ffc7ab2948589abb04200b7dcedd326eaeaf7a4d`. Диагностические лимиты настраиваются; они не являются требованиями будущего продукта. Нет Pause/Continue/StopDebugging или скрытого повтора запросов.

## Граница подтверждения

В установленной среде скрипты совпали, но debugger TCP-listener не предоставлен проверенным Steam-запуском. Alkimia подтвердила отсутствие LSP в 1.1 до следующего обновления. Тесты подтверждают диагностику и локальный сервер; они не доказывают наличие игрового сервера, game thread или обработчика мода. [Результаты среды](../../mods/G1R_MCP/references/2026-10-30-environment-and-transport.md), [контракт dev.1](../../mods/G1R_MCP/local-tools.md).
