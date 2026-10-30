"""Разовая диагностическая проба AngelScript-канала без повтора запросов.

Формат закреплён по Hazelight MIT, Copyright 2019 Hazelight Games AB:
https://github.com/Hazelight/vscode-unreal-angelscript/tree/ffc7ab2948589abb04200b7dcedd326eaeaf7a4d
extension/src/unreal-debugclient.ts, extension/src/debug.ts,
language-server/src/server.ts (запрос базы через секунду после подключения).
Это самостоятельная диагностика, не игровой мод и не MCP-сервер.
"""

import argparse
from dataclasses import dataclass
import json
import re
import socket
import struct
import sys
import time

from angelscript_debug_protocol import (
    MessageType, ProtocolError, ServerFrameDecoder, decode_evaluate,
    decode_server_version, encode_client_frame, encode_evaluate,
)


class ProbeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ProbeOptions:
    host: str = "127.0.0.1"
    port: int = 27099
    timeout: float = 5.0
    max_frame_bytes: int = 16 * 1024 * 1024
    max_total_bytes: int = 64 * 1024 * 1024
    max_messages: int = 256
    request_database: bool = False
    start_debugging: bool = False
    evaluate: str | None = None
    frame_id: int | None = None


def validate_options(options: ProbeOptions) -> None:
    if not 1 <= options.port <= 65535:
        raise ProbeError("invalid_arguments", "Порт должен быть от 1 до 65535")
    if not 0 < options.timeout <= 300:
        raise ProbeError("invalid_arguments", "Timeout должен быть больше 0 и не более 300 секунд")
    if not 1 <= options.max_frame_bytes <= 0xFFFFFFFF or options.max_total_bytes < 1 or options.max_messages < 1:
        raise ProbeError("invalid_arguments", "Диагностические пределы должны быть положительными")
    if options.request_database and options.timeout <= 1:
        raise ProbeError("invalid_arguments", "Для запроса базы timeout должен превышать секунду")
    if options.evaluate is None:
        if options.frame_id is not None:
            raise ProbeError("invalid_arguments", "--frame-id используется только с --evaluate")
        return
    if not options.start_debugging or options.frame_id is None:
        raise ProbeError("invalid_arguments", "--evaluate требует явных --start-debugging и --frame-id")
    expression = options.evaluate
    # Только синтаксические литералы: вызовы, свойства и операторы не выполняются.
    scalar = r"(?:true|false|null|[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    quoted = r'''(?:"(?:[^"\\\r\n]|\\["\\nrt])*"|'(?:[^'\\\r\n]|\\['\\nrt])*')'''
    if not expression.isascii() or not re.fullmatch(f"(?:{scalar}|{quoted})", expression):
        raise ProbeError("invalid_arguments", "Для первичной пробы допустим только простой ASCII-литерал")
    try:
        encode_evaluate(expression, options.frame_id)
    except ProtocolError as error:
        raise ProbeError("invalid_arguments", str(error)) from error


def collect_probe(connection: socket.socket, options: ProbeOptions, result: dict) -> None:
    """Собрать ограниченный ответ одного подключения без автоматического replay."""
    decoder = ServerFrameDecoder(options.max_frame_bytes)
    started = time.monotonic()
    deadline = started + options.timeout
    database_due = started + 1.0
    database_sent = False
    evaluate_sent = False
    if options.start_debugging:
        connection.sendall(encode_client_frame(MessageType.START_DEBUGGING, struct.pack("<i", 2)))
        result["sent_messages"].append(int(MessageType.START_DEBUGGING))
    if options.evaluate is not None:
        connection.sendall(encode_evaluate(options.evaluate, options.frame_id))
        result["sent_messages"].append(int(MessageType.REQUEST_EVALUATE))
        evaluate_sent = True

    while True:
        now = time.monotonic()
        if options.request_database and not database_sent and now >= database_due:
            connection.sendall(encode_client_frame(MessageType.REQUEST_DEBUG_DATABASE))
            result["sent_messages"].append(int(MessageType.REQUEST_DEBUG_DATABASE))
            database_sent = True
        remaining = deadline - now
        if remaining <= 0:
            if decoder.pending_bytes:
                decoder.finish()
            raise ProbeError("timeout", "Истёк срок ожидания диагностического результата; запросы не повторены")
        wait = min(remaining, max(0.001, database_due - now)) if options.request_database and not database_sent else remaining
        connection.settimeout(wait)
        try:
            chunk = connection.recv(min(65536, options.max_total_bytes - result["received_bytes"] + 1))
        except socket.timeout:
            continue
        if not chunk:
            decoder.finish()
            raise ProbeError("peer_closed", "Сервер закрыл соединение до полного результата")
        result["received_bytes"] += len(chunk)
        if result["received_bytes"] > options.max_total_bytes:
            raise ProbeError("limit_exceeded", "Превышен общий диагностический предел получения")
        for frame in decoder.feed(chunk):
            if len(result["messages"]) >= options.max_messages:
                raise ProbeError("limit_exceeded", "Превышен предел количества сообщений")
            result["messages"].append({"type": frame.message_type, "payload_bytes": len(frame.payload)})
            if frame.message_type == MessageType.DEBUG_SERVER_VERSION:
                result["server_version"] = decode_server_version(frame.payload)
            elif frame.message_type == MessageType.DEBUG_DATABASE:
                result["database_chunks"] += 1
                result["database_bytes"] += len(frame.payload)
            elif frame.message_type == MessageType.DEBUG_DATABASE_FINISHED and database_sent:
                result["database_finished"] = True
            elif frame.message_type == MessageType.EVALUATE and evaluate_sent:
                if result.get("evaluation") is not None:
                    raise ProtocolError("Повторный Evaluate не имеет подтверждённой корреляции")
                result["evaluation"] = decode_evaluate(frame.payload)
        complete = (
            "server_version" in result
            and (not options.request_database or result["database_finished"])
            and (options.evaluate is None or result.get("evaluation") is not None)
        )
        if complete and not decoder.pending_bytes:
            result["elapsed_seconds"] = round(time.monotonic() - started, 6)
            return


def run_probe(options: ProbeOptions, connector=socket.create_connection) -> dict:
    """Подключиться один раз; по завершении отправить только Disconnect."""
    result = {
        "ok": False, "endpoint": {"host": options.host, "port": options.port},
        "sent_messages": [], "messages": [], "received_bytes": 0,
        "database_chunks": 0, "database_bytes": 0, "database_finished": False,
        "limitations": [
            "Совместимость G1R определяется этой пробой, а не наличием открытого клиента",
            "Evaluate сопоставляется FIFO с единственным запросом; request_id в протоколе нет",
            "Результат литерала не подтверждает вызов функций, игровой поток или обработчик мода",
            "База возвращается только как количество и размер сообщений, без исходников",
        ],
    }
    connection = None
    try:
        validate_options(options)
        connection = connector((options.host, options.port), timeout=options.timeout)
        connection.settimeout(options.timeout)
        collect_probe(connection, options, result)
        result["ok"] = True
    except ProbeError as error:
        result["error"] = {"code": error.code, "message": str(error)}
    except ProtocolError as error:
        result["error"] = {"code": "protocol_error", "message": str(error)}
    except (OSError, OverflowError, ValueError) as error:
        result["error"] = {"code": "connection_error", "message": str(error)}
    finally:
        if connection is not None:
            try:
                connection.settimeout(min(options.timeout, 0.2))
                connection.sendall(encode_client_frame(MessageType.DISCONNECT))
                result["sent_messages"].append(int(MessageType.DISCONNECT))
            except OSError as error:
                result["disconnect_error"] = str(error)
            finally:
                connection.close()
    return result


class JsonArgumentParser(argparse.ArgumentParser):
    def format_help(self):
        return super().format_help().replace("usage: ", "Вызов: ", 1)

    def error(self, message):
        translated = message.replace("unrecognized arguments:", "Неизвестные параметры:")
        translated = translated.replace("argument ", "Параметр ").replace("expected one argument", "ожидается одно значение")
        translated = re.sub(r"invalid int value: (.*)", r"ожидается целое число: \1", translated)
        translated = re.sub(r"invalid float value: (.*)", r"ожидается число: \1", translated)
        raise ProbeError("invalid_arguments", translated)


def main(argv=None) -> int:
    parser = JsonArgumentParser(
        description="Разовая проба AngelScript: без Pause, Continue и повтора команд",
        add_help=False, usage="%(prog)s [параметры]",
    )
    parser._optionals.title = "Параметры"
    parser.add_argument("-h", "--help", action="help", help="Показать справку и завершить")
    parser.add_argument("--host", default="127.0.0.1", help="Адрес сервера; по умолчанию loopback")
    parser.add_argument("--port", type=int, default=27099, help="Порт; по умолчанию 27099")
    parser.add_argument("--timeout", type=float, default=5.0, help="Срок подключения и сбора результата в секундах")
    parser.add_argument("--max-frame-bytes", type=int, default=16 * 1024 * 1024, help="Предел одного серверного payload")
    parser.add_argument("--max-total-bytes", type=int, default=64 * 1024 * 1024, help="Общий предел полученных байтов")
    parser.add_argument("--max-messages", type=int, default=256, help="Предел полученных сообщений")
    parser.add_argument("--request-database", action="store_true", help="Один раз запросить метаданные базы через секунду после подключения")
    parser.add_argument("--start-debugging", action="store_true", help="Явно отправить StartDebugging с adapter version 2")
    parser.add_argument("--evaluate", help="Один простой ASCII-литерал; произвольные выражения не допускаются")
    parser.add_argument("--frame-id", type=int, help="Явный frame_id для Evaluate; доказанного default нет")
    try:
        options = ProbeOptions(**vars(parser.parse_args(argv)))
        result = run_probe(options)
    except ProbeError as error:
        result = {"ok": False, "error": {"code": error.code, "message": str(error)}}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
