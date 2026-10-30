# Источники G1R MCP

Дата первоначальной сверки: **30.10.2026**. При создании подпроекта использовалось только чтение. После поручения начать PLAN-001 выполнены диагностика, автономные тесты и разрешённые запуски; их [результаты и границы подтверждения](2026-10-30-environment-and-transport.md) описаны отдельно.

Текущий нативный маршрут 1.0.0-dev.2, его адреса и runtime-подтверждения описаны в [нативном контракте](../native-route.md). Исторический отрицательный результат debugger не относится к работоспособности собственного TCP-моста.

## Локальные репозитории

| Источник | Проверенная ревизия | Назначение |
| --- | --- | --- |
| [G1R-Modkit](../../../../../G1R-Modkit/README.md) | `619c2e6d6cc908678c20d63fd4965156b2cbbca9` | Официальные игровые скрипты AngelScript, руководства и примеры; локальная копия не имела изменений при сверке. |
| [MCP to UE4SS](<C:/Users/Vardo/GPTProject/MCP to UE4SS/mods/MCP_to_UE4SS/README.md>) | `ed318a86657a914107eec39a1804979a80fa686d` | Протокол, сервер, каталог возможностей, выполнения и наблюдения; рабочий репозиторий не имел изменений при сверке. |
| [Существующий Lua Bridge](../../../../../docs/mods/G1R_UE4SS_LUA_Bridge_to_MCP/README.md) | Основной репозиторий: `06eb89bc5d81add2cb826bea0d1a4db1b1d0a7bbe` | Существующая связь с игрой, жизненный цикл транспорта и история принятых решений. SHA фиксирует снимок основного репозитория на момент сверки. |

Локальные пути — ссылки на источники исследования, а не зависимости уже существующей реализации. Эти репозитории не скопированы в подпроект.

Полезные разделы `MCP to UE4SS`:

- [Wire-протокол](<C:/Users/Vardo/GPTProject/MCP to UE4SS/mods/MCP_to_UE4SS/source/protocol/README.md>).
- [Контракт сред и каталога возможностей](<C:/Users/Vardo/GPTProject/MCP to UE4SS/mods/MCP_to_UE4SS/source/protocol/environments.md>).
- [Контракт выполнений](<C:/Users/Vardo/GPTProject/MCP to UE4SS/mods/MCP_to_UE4SS/source/protocol/executions.md>).
- [Каталог внешнего сервера](<C:/Users/Vardo/GPTProject/MCP to UE4SS/mods/MCP_to_UE4SS/source/server/src/mcp_to_ue4ss_server/environment_server.py>).
- [Нативный диспетчер](<C:/Users/Vardo/GPTProject/MCP to UE4SS/mods/MCP_to_UE4SS/source/native/src/core/dispatcher.cpp>).

История отказа прежнего моста от файлового mailbox: [рабочий журнал, запись 11.09.2026](../../../../../docs/mods/G1R_UE4SS_LUA_Bridge_to_MCP/work-log.md) и [текущая архитектура](../../../../../docs/mods/G1R_UE4SS_LUA_Bridge_to_MCP/architecture.md). Это факт истории старой реализации, а не доказательство невозможности файлового API в Modkit.

Для подготовки PLAN-001 дополнительно сверены материалы той же ревизии Modkit:

- [Структура пакета и разовые параметры `-Mods` / `-ModsDirectory`](../../../../../G1R-Modkit/Documentation/getting-started/mod-package.md).
- [Режим разработки и ограничения горячей перезагрузки](../../../../../G1R-Modkit/Documentation/getting-started/angelscript-setup.md): тела функций могут перезагружаться; изменение defaults, `UPROPERTY` и добавление `UCLASS` требует перезапуска.
- [Справочник параметров запуска](../../../../../G1R-Modkit/Documentation/reference/commandline-reference.md).
- [Пример WorldSubsystem с `HandleReadyForPlay` и проверкой объекта](../../../../../G1R-Modkit/Examples/Example-Settings/Script/Example-Settings/Settings.as). Пример не доказывает полный жизненный цикл, поток исполнения или пригодность `Tick` для моста.
- [Inheritance Injection](../../../../../G1R-Modkit/Documentation/scripting/script-hooking.md): документированный механизм замены наследования. Его использование в G1R MCP не выбрано и не считается необходимым для связи.

## Официальные источники

Дополнительные источники Э0–Э1:

- [Клиент Hazelight debugger](https://github.com/Hazelight/vscode-unreal-angelscript/tree/ffc7ab2948589abb04200b7dcedd326eaeaf7a4d), закреплённая ревизия для диагностического протокола.
- [UGothicModExt_WorldSubsystem](https://alkimiainteractive.github.io/G1R-Modkit/script-reference/class_u_gothic_mod_ext___world_subsystem.html) — документированные события lifecycle.
- [Официальный Unreal MCP](https://dev.epicgames.com/documentation/en-us/unreal-engine/unreal-mcp-in-unreal-editor) и [UE 5.8 release notes](https://dev.epicgames.com/documentation/en-us/unreal-engine/unreal-engine-5-8-release-notes) — архитектурный источник, не доказательство наличия плагина в G1R.
- [Content Cooking G1R](https://alkimiainteractive.github.io/G1R-Modkit/unreal-project/content-cooking/) — отдельный контур Unreal Editor 5.4 для новых ресурсов.
- [Ответ Alkimia об отсутствующем LSP в 1.1](https://github.com/AlkimiaInteractive/G1R-Modkit/issues/1#issuecomment-6520702135), 30.10.2026: включение обещано в следующем обновлении; фактическая пригодность будущего канала ещё не проверена.

| Источник | Что подтверждено чтением | Что не подтверждено |
| --- | --- | --- |
| [Официальный G1R-Modkit](https://github.com/AlkimiaInteractive/G1R-Modkit) | Источник Modkit; все 7 280 Script-файлов исследованной ревизии совпали с установленным build 26295476. | Фактическое выполнение каждого описанного API; равенство всех компонентов нативной сборки. |
| [Описание ModPackage](https://alkimiainteractive.github.io/G1R-Modkit/getting-started/mod-package/) | Формат официального пакета, включая `GothicMod.json` и каталоги скриптов. | Загрузка ещё не созданного G1R MCP. |
| [JSON API](https://alkimiainteractive.github.io/G1R-Modkit/script-reference/namespace_json.html) | Наличие функций разбора JSON в справочнике. | Фактическая доступность и выполнение в установленной сборке игры. |
| [FFileHelper](https://alkimiainteractive.github.io/G1R-Modkit/script-reference/namespace_f_file_helper.html) | Наличие чтения и записи строк в файлы в справочнике. | Пригодность для канала связи; выбор файлового обмена. |
| [UScriptGameInstanceSubsystem](https://alkimiainteractive.github.io/G1R-Modkit/script-reference/class_u_script_game_instance_subsystem.html) | Описание скриптовой подсистемы и `Tick`. | Точная модель потоков и жизненного цикла будущего моста в игре. |
| [AngelScript Setup](https://alkimiainteractive.github.io/G1R-Modkit/getting-started/angelscript-setup/) | Режим `-as-development-mode`, подключение отладчика и горячая перезагрузка с ограничениями. | Использование отладочного канала как двустороннего IPC или для произвольного внешнего исполнения скриптов. |

Наличие имени или сигнатуры в справочнике подтверждает описание API, но не результат его вызова в текущей игре. Отсутствие найденного сетевого API не является доказательством невозможности связи.
