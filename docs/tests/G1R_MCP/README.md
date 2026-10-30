# Проверки G1R MCP: локальные инструменты и нативный маршрут

Дата: **30.10.2026**. Версия **1.0.0-dev.2**, официальный SDK **mcp 2.3.0**, Python **3.14.6**, MSVC C++20 x64. Нативный маршрут проверен в текущей игре; набор не является приёмкой полного выпуска 1.0.0.

## Автономный набор

Из корня подпроекта:

```powershell
uv sync --locked
.venv\Scripts\python.exe -B -m unittest discover -s tests -q
```

**65 Python-тестов прошли**: 12 для среды, 15 для диагностического debugger-протокола, 12 для источников, 12 для пакетов, 12 для native-клиента и две сквозные MCP stdio-сессии.

- [Среда](../../../tests/test_game_environment.py): allowlist Steam-метаданных, повреждённый ACF, несовпадающий каталог, отсутствующие/различающиеся скрипты, предел списков, symlink и отсутствие изменений файлов.
- [Протокол](../../../tests/test_angelscript_debug_protocol.py): асимметрия направлений, фрагментация и несколько кадров, ограничение/усечение, UTF-8/UTF-16, однократные запросы и отсутствие повтора после timeout. Socketpair заменяет игру.
- [Источники](../../../tests/test_sources.py): разрешённые корни, реальные строки, лексические типы, partial scan, ограничения Unicode-ответа, повреждённый/изменённый файл и выход за корни.
- [Пакеты](../../../tests/test_packages.py): официальный дескриптор, зависимости, content-only пакет, duplicate/NaN/Infinity, неизвестные поля и отсутствие записи.
- [MCP-сессия](../../../tests/test_mcp_server.py): дочерний stdio-сервер, initialize/list tools, все семь вызовов, structured content, ошибки пути/лимита и сохранение входных файлов.
- [Native-клиент](../../../tests/test_native_client.py): framing/Unicode/ID, deadline, обрыв, конкурентные вызовы и отсутствие повтора.
- [MCP ↔ native](../../../tests/test_mcp_native.py): шесть нативных функций, каталог 13 инструментов, параметры и эффект-неопределённость после ошибки без повторного invoke.

## Нативная сборка и автономная проверка

```powershell
.\tools\build_native.ps1
```

CMake собирает DLL, загрузчик и test executable. CTest выполняет **четыре комплексных TCP-сценария**: кадры/Unicode, неправильный JSON/версия/размер/ID, timeout и повторные start/stop, лимит запросов/занятый порт. Зависимости закреплены в `native/CMakeLists.txt`: JSON 3.12.0 и конкретный commit MinHook.

Загруженную DLL перед пересборкой освобождает только закрытие игры. Сборка не запускает игру или загрузчик.

## Проверенная игровая вертикаль

После запуска через Steam и появления главного меню:

```powershell
.\tools\load_native.ps1
.venv\Scripts\python.exe -B tools\probe_native_route.py --enable --call-verified-as
```

В финальной сборке проверены status/ready и вызов `CM_Attack.ShouldInvalidateAttack()` с результатом `false`. Дополнительная реальная MCP-сессия получила 13 инструментов, нашла объект, прочитала свойства и сигнатуру, выполнила один вызов; thread ID результата совпал с игровым Tick. Никакие сохранения не загружались. [Источники, адреса и границы](../../mods/G1R_MCP/native-route.md).

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

Проверен игровой поток и один существующий AngelScript-вызов через самостоятельную DLL. Длительная работа, смена мира, загрузка сохранения, сложные сигнатуры и широкие действия не проверены. История исходной debugger-диагностики хранится в [результатах среды](../../mods/G1R_MCP/references/2026-10-30-environment-and-transport.md); рабочий маршрут описан в [контракте dev.2](../../mods/G1R_MCP/native-route.md).
