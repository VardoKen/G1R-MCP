# Проверки G1R MCP: Э0–Э1

Дата: **30.10.2026**. Нужен Python stdlib; проверено Python 3.14. Рабочего игрового мода/сервера ещё нет, поэтому это диагностика, а не приёмка всего G1R MCP.

## Автономный набор

Из корня подпроекта:

```powershell
python -B -m unittest discover -s tests -q
```

**27 тестов прошли**: 12 для среды и 15 для протокола/разовой пробы.

- [Среда](../../../tests/test_game_environment.py): allowlist Steam-метаданных, повреждённый ACF, несовпадающий каталог, отсутствующие/различающиеся скрипты, предел списков, symlink и отсутствие изменений файлов.
- [Протокол](../../../tests/test_angelscript_debug_protocol.py): асимметрия направлений, фрагментация и несколько кадров, ограничение/усечение, UTF-8/UTF-16, однократные запросы и отсутствие повтора после timeout. Socketpair заменяет игру.

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

В установленной среде скрипты совпали, но debugger TCP-listener не предоставлен проверенным Steam-запуском. Тесты подтверждают диагностический код и контракт открытого клиента, а не наличие сервера G1R, game thread или обработчика мода. [Подробные результаты и метод](../../mods/G1R_MCP/references/2026-10-30-environment-and-transport.md).
