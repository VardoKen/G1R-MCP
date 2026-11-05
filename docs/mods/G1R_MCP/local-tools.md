# Локальные MCP-инструменты — 1.0.0-dev.112

Дата сверки: **05.11.2026**. Документ описывает текущую реализацию в `src/g1r_mcp/` и `native/src/`. Сервер использует официальный Python SDK `mcp==2.3.0`; в `server.py` зарегистрирован **52 инструмента**. Схемы аргументов задаёт регистрация MCP, а `g1r_capabilities` дополняет их происхождением данных, доступностью, форматами и ограничениями. Ответы и ошибки моста — на английском языке; проектная документация — на русском.

Назначение G1R MCP — полноценное исследование, разработка и отладка официальных модов, а перенос пользовательских модов с UE4SS — промежуточный рубеж. Рабочие пакеты — официальные `GothicMod.json` и AngelScript. Каталог ниже описывает реализованные инструменты, а не завершение всей цели. Игровой маршрут, включая загрузку DLL и выполнение через Tick, описан в [native-route.md](native-route.md); сводка готового и неготового — в [README.md](README.md), доказательства — в [описании проверок](../../tests/G1R_MCP/README.md).

Dev.112: `g1r_debug_database` получил полную runtime-базу: 5 262 chunks, 20 151 types, 119 789 flat entries, 9 470 492 bytes, 1,394 с; settings type31/version5/float64=true, отдельного type33 не было. Общий cache принадлежит серверу; query не открывает TCP и работает после закрытия игры. Это raw debugger catalog, не доказательство UFunction-доступности или полного каталога собственных AS-пакетов. [Подтверждения](../../../artifacts/dev112-debug-database-live-complete.json).


## Каталог 52 инструмента

Python-сервер — **1.0.0-dev.112**, native DLL — **1.0.0-dev.111**, 52 инструмента. Полная debugger database и прежние 46/46 typed-вызовов, 9/9 итоговых проверок приняты. [Результат dev.112](../../../artifacts/dev112-debug-database-live-complete.json).

Dev.103: int32/FName input-массивы и собственные простые структуры с числовыми/bool-полями копируются в принадлежащий вызову буфер по повторно проверенной reflection-раскладке и identity. Направление input и передача value/const_reference определяются фактическими флагами: const reference с CPF_OutParm нормализуется как input. Поддержка чтения произвольного DTO не означает поддержку его как аргумента; FName-элемент принимает {name:string} или {name_from:{handle,property_name}}; текстовый FName создаётся с Number=0, источник name_from повторно проверяется. Другие элементы массива и вложенные структуры этой приёмкой не подтверждены.

В таблицах перечислены пользовательские аргументы. Контекст MCP-сессии `ctx` передаётся SDK. Необязательный `timeout_seconds` игровых запросов меняет ожидание ответа, но не доказывает отмену уже исполняемого вызова.

### Метаданные и официальные исходники — 10

| Инструмент | Аргументы | Назначение |
| --- | --- | --- |
| `g1r_status` | Нет | Версия, готовность сервера и настроенные корни; без проверки живой игры |
| `g1r_capabilities` | Нет | Каталог 52 инструмента, bootstrap, форматы чтения/вызова и границы поддержки |
| `g1r_debug_database` | `query=""`, `limit=100`, `offset=0`, `refresh=false`, `timeout=30.0` | Полная raw runtime-база debugger; explicit refresh подключается к listener один раз, query читает общий cache без TCP |
| `g1r_source_identity` | Нет | Git-метаданные настроенного Modkit; отдельно от caller label `--source-revision` |
| `g1r_verify_source_game` | `hash_executable=false` | Последовательное сравнение источников с установленной игрой; опциональный SHA-256 EXE |
| `g1r_find_source_files` | `query`, `limit=50`, `offset=0`, `relative_package` | Поиск путей официальных исходников либо одного рабочего пакета |
| `g1r_search_source` | `query`, `limit=50`, `case_sensitive=false`, `offset=0`, `relative_package` | Буквальный поиск строк с путём и номером строки |
| `g1r_read_source` | `relative_path`, `start_line=1`, `line_count=100` | Построчный просмотр официального исходника и SHA-256 полных байтов |
| `g1r_read_source_exact` | `relative_path` | Полный UTF-8 исходник с сохранением BOM, CRLF и EOF |
| `g1r_list_source_types` | `query=""`, `limit=50`, `offset=0`, `relative_path` | Лексические объявления class/struct/enum, namespace, исходное объявление и базовый тип |

### Рабочий пакет и экспорт — 11

| Инструмент | Аргументы | Назначение |
| --- | --- | --- |
| `g1r_list_source_functions` | `query=""`, `relative_path`, `limit=50`, `offset=0` | Лексические объявления функций/методов, исходный prototype, владелец, строки и SHA-256 |
| `g1r_validate_package` | `relative_path` | Структура дескриптора, компоненты, скрипты/патчи и диагностика |
| `g1r_create_package` | `relative_path`, `metadata` | Создание нового дескриптора и пустого `Script/` |
| `g1r_create_package_directory` | `relative_path`, `directory_path` | Создание каталога внутри разрешённого компонента пакета |
| `g1r_list_package_files` | `relative_path`, `limit=100`, `offset=0` | Страница дескриптора и файлов официальных компонентов |
| `g1r_read_package_file` | `relative_path`, `file_path`, `start_line=1`, `line_count=100` | Построчный просмотр рабочего файла и SHA-256 |
| `g1r_read_package_file_exact` | `relative_path`, `file_path` | Полный UTF-8 текст рабочего файла без нормализации |
| `g1r_write_package_file` | `relative_path`, `file_path`, `content`, `expected_sha256` | Создание файла либо замена при совпадении ожидаемого SHA-256 |
| `g1r_build_script_patch` | `source_path`, `replacement_content`, `expected_source_sha256`, `context_lines=3` | Unified diff официального `Script/**/*.as`; исходник не изменяется |
| `g1r_export_package` | `relative_package`, `destination` | ZIP полного пакета и SHA-256 manifest в новом каталоге `artifacts/` |
| `g1r_verify_package_export` | `relative_path` | Проверка содержимого архива, manifest и хешей экспортированного комплекта |

### Development-сеанс, владение и DLL — 14

| Инструмент | Аргументы | Назначение |
| --- | --- | --- |
| `g1r_session_status` | Нет | Текущая заявка владельца общей игры; без игрового Tick |
| `g1r_session_acquire` | `reason`, `expected_process`, `expected_claim_id` | Получение владения текущей MCP-сессией с проверкой идентичности |
| `g1r_session_release` | `expected_claim_id` | Освобождение заявки без остановки игры; чужая заявка освобождается только после исчезновения игры |
| `g1r_compile_diagnostics` | `relative_package`, `expected_manifest_sha256`, `report_path`, `enable_ingame_tools=false`, `show_script_errors=false` | Native compiler formatter либо явно выбранный UTF-8 отчёт |
| `g1r_development_recipe` | `relative_package`, `enable_ingame_tools=false`, `companion_packages`, `show_script_errors=false` | Manifest выбранных пакетов, запуск, процессы и SHA-256 DLL |
| `g1r_development_processes` | Нет | PID, время создания и полный путь EXE выбранной игры и её wrapper |
| `g1r_apply_staged_file` | `relative_package`, `file_path`, `expected_source_sha256`, `expected_staged_sha256`, `expected_process` | Применение одного canonical-файла к активной staging-копии собственного сеанса |
| `g1r_development_key` | `expected_process`, `virtual_key`, `expected_window` | Очередь выбранного ввода окну проверенного процесса; обработка игрой не подтверждается |
| `g1r_development_start` | `relative_package`, `expected_manifest_sha256`, `restart=false`, `expected_process`, `enable_ingame_tools=false`, `session_reason`, `companion_packages`, `show_script_errors=false` | Запуск проверенного набора staging-пакетов; restart требует исходную идентичность |
| `g1r_development_stop` | `expected_process`, `reason` | Остановка собственного проверенного сеанса |
| `g1r_native_load` | `expected_process`, `expected_dll_sha256` | Однократная загрузка закреплённой DLL |
| `g1r_native_status` | Нет | Живые ready/Tick, очередь, identity, поколения и состояние native-подключения |
| `g1r_native_enable` | Нет | Установка игрового Tick hook; ready проверяется отдельно |
| `g1r_movieplayer_status` | Нет | Состояние MoviePlayer из native cache, доступное без игрового Tick |

### Игровые значения, наблюдения и действия — 17

| Инструмент | Аргументы | Назначение |
| --- | --- | --- |
| `g1r_movieplayer_finish` | `expected_wait_id`, `timeout_seconds` | Явное завершение наблюдаемого MoviePlayer wait в игровом контексте |
| `g1r_find_objects` | `query=""`, `class_query=""`, `limit=50`, `exact_path=""` | Поиск текущих объектов и выдача session handles |
| `g1r_object_properties` | `handle`, `limit=50`, `property_names` | Метаданные и доступные значения; точные имена возвращаются в порядке запроса |
| `g1r_object_functions` | `handle`, `limit=50`, `function_query=""` | Отражённый контракт, типы параметров и `signature_token` |
| `g1r_read_array` | `handle`, `property_name`, `offset=0`, `limit=64` | Страница отражённого массива, включая структуры и вложенные массивы |
| `g1r_read_struct` | `handle`, `property_name` | Копия собственных отражённых полей структуры с отдельными ошибками дочерних значений |
| `g1r_read_set` | `handle`, `property_name`, `offset=0`, `limit=64` | Чтение Set с точным элементом `NameProperty` |
| `g1r_read_map` | `handle`, `property_name`, `offset=0`, `limit=64` | Чтение Map с ключом `NameProperty` и значением `ObjectProperty` |
| `g1r_snapshot` | `targets` | Последовательный снимок выбранных свойств объектов на одном Tick |
| `g1r_compare_snapshots` | `before`, `after`, `limit=100` | Локальное сравнение копий; значения, типы, охват и неизвестные результаты |
| `g1r_watch_start` | `handle`, `properties`, `duration_ms`, `interval_ms`, `capacity=32` | Ограниченная выбранными параметрами запись property-наблюдения |
| `g1r_watch_read` | `watch_id`, `after_seq=0`, `limit=100` | Страница событий и сведения о потерях/завершении |
| `g1r_watch_stop` | `watch_id` | Остановка наблюдения |
| `g1r_watch_forget` | `watch_id` | Удаление его сохранённых данных |
| `g1r_watch_list` | Нет | Обнаружение наблюдений текущего native-подключения |
| `g1r_watch_resume` | `watch_id`, `expected_session_id`, `expected_client_owner` | Смена владельца существующего наблюдения без повторного запуска |
| `g1r_invoke` | `handle`, `function`, `arguments`, `expected_signature_token` | Один типизированный вызов подтверждённого контракта |

Создание/запись/экспорт, apply-staged, владение, запуск/остановка/загрузка/enable, ввод, MoviePlayer finish, управление наблюдениями и invoke имеют эффекты. Сравнение снимков, чтение файлов/значений, каталог и диагностика читают данные. `g1r_status` и `g1r_capabilities` не обращаются к живой игре: `connection_checked=false` и `ready_known=false` не означают её проверенную доступность.

## Источники и полнота результатов

Официальный источник ограничен `Script/` и `Examples/<пример>/Script/` настроенного Modkit. `relative_package` у двух поисковых инструментов переключает область на `Script/` и `ScriptPatches/` одного рабочего пакета; результат явно маркирует эту область. Каталог Modkit защищён от записи даже при вложенности в workspace. Метаданные ревизии и сравнение с установленными байтами не доказывают доступность каждого API из runtime.

Типы и функции разбираются лексически, без компилятора и разрешения наследования. Возвращаются исходные объявления/prototypes, строки, путь, SHA-256 и `root_kind`; `runtime_type_confirmed` и `runtime_signature_confirmed` остаются `false`. Фильтр `relative_path` позволяет разобрать один официальный файл. При пропущенных файлах результат содержит причины и нижнюю границу числа совпадений вместо недоказанного полного числа.

Поиск и списки используют `offset`/`next_offset`/`has_more`. Обход и чтение последовательны, не являются атомарным снимком каталогов. Страницы могут измениться между запросами. Прежние произвольные пределы размера файлов, количества результатов/файлов и глубины рабочего пути в dev.99 не действуют; `None`/`null` в metadata означает отсутствие соответствующего прикладного потолка, а не неограниченность памяти ОС или native ABI. Положительный `limit` по-прежнему задаёт размер страницы. UTF-8, корректность путей, существование объектов и отражённый размер проверяются.

## Работа с пакетом и патчем

В рабочей базе поддержаны `GothicMod.json` и пять официальных компонентов: `Script`, `Config`, `Content`, `Story`, `ScriptPatches`. Создание пакета требует отсутствующий каталог с существующим родителем; создаются только дескриптор и пустой `Script/`, без выдуманного обязательного `main.as`. Пакет контента может не иметь `Script/`; пакет только с патчами проходит структурную проверку.

Построчное чтение предназначено для просмотра и возвращает SHA-256 полных исходных байтов. Для полной замены использовать exact-чтение: `text.encode("utf-8")` воспроизводит BOM, CRLF и отсутствие завершающего LF. Binary-файлы компонентов включаются в список, manifest и экспорт; текстовые операции требуют корректный UTF-8.

Без `expected_sha256` разрешено только создание отсутствующего файла; замена требует совпадения текущих полных байтов. Изменённый или исчезнувший файл даёт `conflict`. Кандидат дескриптора валидируется до записи. Записи назначения сериализованы между MCP-процессами; произвольный внешний редактор не становится частью этой блокировки. При отказе посторонние файлы сохраняются. Пути с выходом из разрешённых корней, NUL, Windows device names/alternate streams, symlink/junction/reparse points отвергаются.

Валидатор подтверждает `valid_structure`, а не принятие дескриптора игровым загрузчиком, компиляцию или загрузку. `modVersion` проверяется как непустая строка; прохождение JSON-проверки не доказывает совместимость произвольной строки версии. Duplicate keys и неверные типы не скрываются. `compiled=false`, `runtime_validation="not_performed"` и `patch_validation="not_performed"` сохраняют эту границу.

`g1r_build_script_patch` требует официальный `Script/**/*.as` и совпадение полного SHA-256. Формат — стандартный unified diff; сохраняются BOM, CRLF и EOF, одиночный CR отвергается. Одинаковые байты дают пустой diff; конфликт не исправляется скрытой перезаписью. Diff записывается в рабочий `ScriptPatches/` отдельной операцией. Построение и локальная валидация не применяют патч к игре. Фактическое применение официальным загрузчиком проверено отдельно; `g1r_capabilities.bootstrap.script_patch_engine_apply_confirmed=true` не является подтверждением каждого нового патча.

Экспорт теперь доступен через MCP. ZIP и manifest включают дескриптор и файлы всех пяти компонентов; неизвестные корневые компоненты отвергаются. Байты сохраняются, проверки сверяют список и хеши. Экспорт не компилирует, не устанавливает пакет и не публикует его в Steam.

## Development-сеанс и диагностика

Recipe закрепляет полный manifest основного и явно выбранных companion-пакетов, launch options и SHA-256 DLL. `enable_ingame_tools` и `show_script_errors` принимают JSON bool, входят в manifest и по умолчанию выключены. Для смены набора пакетов или этих флагов нужен новый рецепт. Start создаёт отдельные staging-копии под `artifacts/`, запускает их из внешнего каталога и не заменяет canonical-исходники. Пути к staging и manifest возвращаются в ответе; копии сохраняются для проверки.

Start/restart, stop и load сверяют PID, время создания и полный EXE. Restart требует исходную идентичность. Load закрепляет SHA-256 DLL; `load_result` с вложенным `loaded` подтверждает загрузку, но не Tick. `launch_requested` подтверждает только запрос Steam. Native DLL не выгружается: её завершает закрытие игры.

Для hot reload сначала прочитать canonical и staging exact-файлы, записать canonical с ожидаемым хешем и вызвать `g1r_apply_staged_file` с обоими ожидаемыми хешами и process identity. Ответ подтверждает применение файла, а не завершение компиляции. File watcher применим к телам функций; новые классы, поля и defaults требуют restart. Поколение scripts считает успешные возвраты наблюдаемого native reload; не подтверждает компиляцию конкретного SHA-256 и не увеличивается при неудачной попытке.

Без `report_path` `g1r_compile_diagnostics` читает native compiler formatter; с `report_path` читает явно предоставленный рабочий `.txt`/`.log`. Возвращаются сообщения, severity, файл, line/column и, когда сопоставление найдено, SHA-256 текущего исходника. Dev.100 дополнительно возвращает `last_completed_reload`, счётчики начатых/завершённых попыток, согласованность reload-контекста при compiler-чтении, хеши выбранного и связанного staged-пакета до/после чтения и совпадения полных абсолютных AS-путей. Значение двух полей пары changed-file остаётся неизвестным; basename и суффикс пути не принимаются за идентичность файла. `compiler_api_available=true` у native-маршрута означает доступ к formatter. Ни пустой отчёт, ни SHA-256 текущего файла, ни успешный reload с совпавшим путём не доказывают компиляцию этих байтов: `compiled=false`, `exact_source_compiled=false`, `report_matches_runtime_compile=false`, `source_current_correlation="unknown"`. Ошибки текущего AngelScript invoke возвращаются отдельно как `angelscript_exception`/`script_errors`, с наблюдаемыми сведениями о стеке; отсутствие наблюдаемого сообщения не доказывает завершение произвольного скрипта. Live dev.101 проверила эту диагностику на одной игре: успешная правка тела, одна ошибка компиляции и восстановление; raw receipt и exact path-match сохранены в `artifacts/dev101-name-reload-live-final.json`.

Development helper поставляется отдельным companion-пакетом `development/helpers/G1RMCPDevelopmentHelpers`. Он предоставляет отражённые `GetHelperVersion`, `ReadText`, `HasWorld`, `HasGameplayPlayer`, `GetVisibilityTraceChannel`, `TraceImpactPoint`. Живой world-instance и явное включение пакета нужны для его игровых операций; наличие helper в bundle само его не активирует.

## Отражённое состояние и ссылки

`g1r_object_properties` допускает точный непустой список уникальных имён без NUL. Missing, неподдержанный тип и неполный охват выдаются явно. Отдельные контейнерные/struct-инструменты расширяют чтение; metadata-only результат `object_properties` не следует автоматически считать недоступностью соответствующего отдельного reader.

`g1r_read_array` поддерживает скаляры, bool, enum/Byte, FName, FString, Object/Class/Weak-ссылки, Struct и Array по отражённому layout. `ArrayDim=1`, корректность header, размера и доступной памяти проверяются. Ответ содержит `items`, `offset`, `next_offset`, `has_more`, `truncated`, ошибки элементов и `partial`. EOF имеет `next_offset=null` и `has_more=false`. Страницы не образуют атомарный снимок.

`g1r_read_struct` читает собственные поля без super-структуры, с совпадением отражённого размера/выравнивания. Поддержаны bool, 32/64-битные целые, float/double, enum/Byte, FName/FString, Object/Class/Weak, вложенные Struct и Array. Известны Vector, Rotator, Transform, LinearColor, Quat; неизвестная пользовательская структура тоже допустима при подтверждённом own-field layout. Вложенные массивы структур содержат `value` и метаданные/ошибки дочерних элементов. Неподдержанное дочернее поле даёт отдельную ошибку и `partial=true`; оно не превращает весь результат в доказанное полное чтение. Вложенные Set/Map, SoftObject/SoftClass, Optional, делегаты и неподтверждённые layouts не поддержаны этим reader.

`g1r_read_set` требует отражённый Set с FName-элементом. `g1r_read_map` требует FName-ключ и UObject-значение. Порядок/индекс отражает sparse storage, а не сортировку; пустые/free slots не выдаются за элементы. Memory-image allocator Map и остальные типы Set/Map отвергаются. Заимствованные buffers читаются без освобождения или изменения игрового контейнера.

Object/Class значения включают тип и session descriptor; Weak читается через текущую slot identity и serial, с проверкой доступности/типа. `null` допустим там, где отражённая ссылка пуста. `serial_guard.policy="strict_observed_equality_including_zero"`; наблюдаемые pointer/index/FName/serial объекта и класса должны совпасть. Нулевые serial не защищают от ABA, мост их не выделяет самостоятельно. Для мировых handles действует поколение `GameEngine.GameViewport.World`; world change завершает соответствующие watches и отзывает связанные handles. CDO/assets сохраняются при смене мира; script reload отдельно отзывает script-bound handles. Disconnect/reconnect отзывает handles и наблюдения всего native-подключения.

`g1r_snapshot` и сравнение сохраняют различия значения, типа, охвата и неизвестного результата. `single_tick_sequential` не означает атомарность с произвольным внешним игровым кодом. Неполный охват не доказывает равенство; отсутствие цели не доказывает уничтожение объекта. Watches — опрос выбранных properties, а не произвольная подписка на игровые delegate-события. Их metadata включает причины завершения и потери; `watch_resume` передаёт существующее наблюдение по точным session/owner identity.

## Контракт invoke

`g1r_object_functions` фильтрует по `function_query` до ограничения выдачи. Полный поддержанный контракт получает `signature_status="complete"` и `signature_token`; неподдержанный остаётся видимым с причиной и `signature_token=null`. `expected_signature_token` в invoke необязателен; при передаче проверяет объект, функцию, поколения и полную reflection перед `ProcessEvent`. Без токена проверка типов и identity сохраняется.

Поддержка определяется направлением и точным layout каждого параметра, а не одним именем типа:

- Scalar/bool, 32/64-битные целые, finite float/double, enum/Byte — по диапазону и отражённому размеру. Enum-вход — число объявленного значения; строковые labels не являются входом.
- FName — копия существующего значения через `{"name_from":{"handle":"…","property_name":"…"}}`; by-value и const-reference input, возврат. Произвольное создание имени из строки не поддержано.
- FString — JSON string, by-value/const-reference input и возврат; выделенные engine buffers имеют отдельный учёт владения/очистки.
- UObject/Class — `null` или `{"handle":"…"}`, с проверкой declared type/meta class.
- FVector — input/const-reference, pure output и by-value return; FRotator/FTransform — input/const-reference и by-value return. FLinearColor — by-value input/return. FGameplayTag — input с единственным TagName через существующий FName, by-value/const-reference.
- Собственная DTO и её массив — own numeric/bool/FName/byte enum и вложенные own Struct по рекурсивной reflection. Single DTO поддерживает by-value input/return и pure output; массив — value/const-reference input, by-value return и pure output. FName внутри DTO — `{name:string}`, без nested name_from; metadata содержит struct/enum/bool. FString/custom lifecycle и inout не поддержаны.
- TArray<FString> — plain JSON string input, value/const-reference input, by-value return и pure output; пустые/Unicode строки и дубликаты допустимы, embedded NUL отвергается, внутренние строки и внешний массив имеют общий подтверждаемый cleanup. Nested DTO FString этим не поддержаны.
- Базовые scalar-массивы Bool/Int32/Int64/UInt32/UInt64/Float/Double — input/return/pure output по отражённой ширине элемента и bool masks; exact integer ranges и finite float, старый int_array контракт сохранён.
- TArray<int32>/TArray<FName>/однобайтовый enum — input и by-value return в подтверждённом layout; возврат `{items, total_count, partial}` копирует значения с проверкой metadata и очисткой контейнера. TArray<UObject> — const-reference input, by-value return и отдельный pure output; массив однобайтового enum — подтверждённый input. TSet<UObject> — by-value return. Контейнеры копируют pointers, не владеют самими UObject.
- Scalar DTO — только by-value return с собственными отражёнными scalar-полями; это более узкий контракт, чем вложенное чтение состояния. Generic nested Struct/Array<Struct> invoke, Map, Weak/Soft/Optional и delegate-аргументы не добавлены.
- Pure scalar, TArray<UObject>/FVector и int32/FName/enum/scalar DTO array output запрашиваются `{"out":true}` по отражённому имени. Ответы — `out_parameters`, metadata — `out_parameter_metadata`; pure output с подтверждённой очисткой. Inout, произвольный mutable reference и неподтверждённые out-типы отвергаются.

После исполнения ошибка копирования/очистки или native/script fault сохраняет признаки эффекта. `may_have_executed`, `outcome_unknown` и `do_not_retry` запрещают автоматический повтор; отказ до исполнения отличается от неизвестного результата уже начавшегося вызова. Перед новым вызовом после invalidation требуется свежий handle/контракт. Invoke не является произвольным AngelScript eval.

## Сверка согласованного PLAN-001

51 зарегистрированный инструмент покрывает конкретные части [PLAN-001](plans/PLAN-001-active-implementation.md), но не подтверждает завершение всей цели исследования, разработки и отладки. Текущие статусы различаются:

- **Реализовано и принято в игре:** выбранные native Tick/ProcessEvent контракты, чтение объектов и поддержанных значений, вложенные Struct/Array DTO, snapshots/watches; перезагрузка тел функций и два конкретных ScriptPatches подтверждены историческими игровыми пробами. Для нового вызова, пакета или патча требуется собственный результат.
- **Реализовано локально:** каталог и чтение исходников, редактура/структурная проверка пакетов, построение ScriptPatches, экспорт и проверка ZIP. `valid_structure` и успешная запись не означают компиляцию, загрузку или применение патча.
- **Механизм реализован, подтверждение результата неполно:** native compiler formatter, чтение предоставленного отчёта и staged-file hot reload. `compiler_api_available=true` не является API произвольной компиляции; `compiled=false`, `report_matches_runtime_compile=false` и неизвестная связь отчёта с SHA-256 сохраняются. Scripts-generation наблюдает успешный возврат reload, а не подтверждает компиляцию выбранных байтов.
- **Диагностика реализована частично:** ошибки наблюдаемого AngelScript invoke и стек относятся к текущему `ProcessEvent`. Полная история runtime-ошибок и debugger с breakpoint/step/locals не реализованы этим каналом.
- **Готового контракта нет:** произвольный eval/VM-компиляция, универсальный invoke вложенных DTO/контейнеров и общая delegate-подписка. Создание и cooking assets относятся к Unreal Editor; текстовая работа и экспорт существующего `Content/` их не заменяют.

Полная сводка и источники доказательств находятся в [README.md](README.md) и [проверках](../../tests/G1R_MCP/README.md); исторические результаты не переносятся автоматически на новый API или каноническую DLL dev.99.

Конкретные границы dev.99: нет универсального invoke вложенных DTO/контейнеров, произвольного eval, общей delegate-подписки и source-SHA-correlated подтверждения `compiled=true`; cooking ресурсов требует редакторского контура. Эти функции не объявляются действующими. У согласованных групп не найден отсутствующий инструмент целиком; ограничения подтверждённых типов и compiler provenance описаны выше, без заявления о поддержке любого контракта.

## Исторические подтверждения

Следующие результаты сохраняют историю и не заменяют текущую матрицу возможностей:

- Dev.1: на тогдашнем Modkit просмотрены **7 292** файла (7 280 игровых, 12 примеров), найден `UGameTimeConfigG1R`. Полный лексический проход занимал около **32 секунд**, два файла больше прежних 2 МиБ (`WorldPointManagerConfig_MainMap.as`, `VoiceTypes.as`) пропускались. Это прежний предел и измерение, не текущий dev.99 ceiling или замер скорости.
- Dev.7: нулевые входы с FName/FString-возвратом прошли MCP; FString освобождался после копирования. Эта проба не подтверждала input/out/ref.
- Dev.8: enum/Byte underlying размером 1 прошёл для 0/1/7, девять неправильных входов не изменили счётчик.
- 31.10.2026, dev.9/dev.11: `enable_ingame_tools=true` подтвердил автоматический Initialize собственного UIngameTool в главном меню, CDO не инициализировался; позднее WorldSubsystem PostInitialize подтвердился один раз, ReadyForPlay той пробой не проверялся. Отрицательный ранний world-override результат не был доказательством отсутствия всех world hooks.
- Dev.10/dev.14: введены страницы массивов, простые struct-копии, signature tokens и точная выборка свойств. Тогдашние ограничения числовыми структурами, отсутствием вложенного чтения, 38 инструментами и фиксированными размерами больше не описывают dev.99.
- Dev.11: FName `name_from` для Entry/None и десять отказов без изменения счётчика подтверждены объединённой live-пробой; произвольное создание FName этим не проверялось.
- Dev.12: общий HTTP-сервер, независимые клиентские сессии и владение start/stop проверены. Native reconnect отзывает общие ссылки; локальное освобождение HTTP-владения не равно новой игре.
- Проба патча автономно прошла `git apply --check` и применение к изолированному исходнику; игровой engine apply подтверждён отдельным поздним результатом. Старое утверждение «экспорт только CLI» заменено MCP export/verify.
