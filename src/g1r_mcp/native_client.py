"""Последовательный клиент локального нативного канала без повтора команд."""

import ipaddress
import json
import math
import socket
import struct
import threading
import time
from typing import Any
import uuid


PROTOCOL_VERSION = 1
DEFAULT_PORT = 27123
MAX_FRAME_BYTES = 1024 * 1024
MAX_TIMEOUT = 5.0


class NativeError(Exception):
    """Ошибка канала; потерянный ответ не подтверждает отсутствие эффекта."""

    def __init__(self, code: str, message: str, may_have_executed: bool = False,
                 request_id: str | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.may_have_executed = may_have_executed
        self.request_id = request_id
        self.details = dict(details or {})


def _timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Таймаут должен быть числом")
    value = float(value)
    if not math.isfinite(value) or not 0 < value <= MAX_TIMEOUT:
        raise ValueError("Таймаут должен быть больше нуля и не превышать 5 секунд")
    return value


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Повторяющееся поле JSON")
        result[key] = value
    return result


def _reject_constant(value: str):
    raise ValueError("Недопустимая константа JSON")


class NativeClient:
    """Одно постоянное соединение, один запрос в полёте, общий deadline."""

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
                 timeout: float = MAX_TIMEOUT):
        if host == "localhost":
            host = "127.0.0.1"
        try:
            address = ipaddress.ip_address(host)
        except (TypeError, ValueError):
            raise ValueError("Разрешён только числовой loopback-адрес или localhost") from None
        if not address.is_loopback:
            raise ValueError("Нативный канал разрешён только на loopback-адресе")
        if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
            raise ValueError("Порт должен быть целым числом от 1024 до 65535")
        self.host = str(address)
        self.port = port
        self.timeout = _timeout(timeout)
        self._family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
        self._request_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._socket: socket.socket | None = None
        self._closed = False

    def _remaining(self, deadline: float, request_id: str, sent: bool) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise NativeError("native_timeout", "Истёк срок ожидания нативного ответа", sent, request_id)
        return remaining

    def _closed_error(self, request_id: str, sent: bool) -> NativeError:
        return NativeError("native_closed", "Клиент нативного канала закрыт", sent, request_id)

    def _connect(self, deadline: float, request_id: str) -> socket.socket:
        with self._state_lock:
            if self._closed:
                raise self._closed_error(request_id, False)
            if self._socket is not None:
                return self._socket
            stream = socket.socket(self._family, socket.SOCK_STREAM)
            self._socket = stream
        try:
            stream.settimeout(self._remaining(deadline, request_id, False))
            stream.connect((self.host, self.port))
            with self._state_lock:
                if self._closed:
                    raise self._closed_error(request_id, False)
            return stream
        except NativeError:
            self._drop(stream)
            raise
        except TimeoutError:
            self._drop(stream)
            with self._state_lock:
                if self._closed:
                    raise self._closed_error(request_id, False) from None
            raise NativeError("native_timeout", "Истёк срок подключения к нативному каналу", False, request_id) from None
        except OSError:
            self._drop(stream)
            with self._state_lock:
                if self._closed:
                    raise self._closed_error(request_id, False) from None
            raise NativeError("native_unavailable", "Нативный канал недоступен", False, request_id) from None

    def _drop(self, stream: socket.socket):
        with self._state_lock:
            if self._socket is stream:
                self._socket = None
        try:
            stream.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        stream.close()

    def _receive(self, stream: socket.socket, size: int, deadline: float,
                 request_id: str) -> bytes:
        result = bytearray()
        while len(result) < size:
            stream.settimeout(self._remaining(deadline, request_id, True))
            fragment = stream.recv(size - len(result))
            if not fragment:
                raise NativeError("native_disconnected", "Нативный канал закрыт до получения ответа", True, request_id)
            result.extend(fragment)
        return bytes(result)

    def _decode(self, payload: bytes, request_id: str) -> dict[str, Any]:
        try:
            response = json.loads(payload.decode("utf-8"), object_pairs_hook=_json_object,
                                  parse_constant=_reject_constant)
        except (UnicodeError, ValueError, RecursionError):
            raise NativeError("native_protocol_error", "Нативный ответ содержит некорректный UTF-8 JSON", True, request_id) from None
        if not isinstance(response, dict) or type(response.get("protocol")) is not int or response["protocol"] != PROTOCOL_VERSION:
            raise NativeError("native_protocol_error", "Некорректная версия или форма нативного ответа", True, request_id)
        if response.get("id") != request_id:
            raise NativeError("native_protocol_error", "Нативный ответ относится к другому запросу", True, request_id)
        if response.get("type") == "response" and isinstance(response.get("result"), dict):
            return response["result"]
        if response.get("type") == "error":
            error = response.get("error")
            if (isinstance(error, dict) and isinstance(error.get("code"), str)
                    and 0 < len(error["code"]) <= 128 and isinstance(error.get("message"), str)
                    and len(error["message"]) <= 8192):
                # Формат не обещает отсутствие эффектов при ошибке обработчика.
                details = error.get("details")
                if details is not None:
                    if not isinstance(details, dict) or any(
                        key in details and type(details[key]) is not bool
                        for key in ("executed", "may_have_executed", "do_not_retry")
                    ) or ("cause_code" in details and not isinstance(details["cause_code"], str)):
                        raise NativeError("native_protocol_error", "Некорректные сведения об эффекте команды", True, request_id)
                    may_have_executed = details.get("may_have_executed", False) or details.get("executed", False)
                else:
                    may_have_executed = True
                raise NativeError(error["code"], error["message"], may_have_executed, request_id, details)
        raise NativeError("native_protocol_error", "Нативный ответ не содержит результата или корректной ошибки", True, request_id)

    def request(self, op: str, params: dict[str, Any] | None = None,
                *, timeout: float | None = None) -> dict[str, Any]:
        """Отправить одну команду. Повтор после сбоя выполняется только новым явным вызовом."""
        if not isinstance(op, str) or not op or any(ord(char) < 32 for char in op):
            raise ValueError("Имя операции должно содержать от 1 до 64 байтов UTF-8 без управляющих символов")
        try:
            encoded_op = op.encode("utf-8")
        except UnicodeError:
            raise ValueError("Имя операции не представимо UTF-8") from None
        if len(encoded_op) > 64:
            raise ValueError("Имя операции превышает 64 байта UTF-8")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise ValueError("Параметры операции должны быть объектом")
        duration = self.timeout if timeout is None else _timeout(timeout)
        request_id = uuid.uuid4().hex
        try:
            payload = json.dumps({"protocol": PROTOCOL_VERSION, "id": request_id, "op": op, "params": params},
                                 ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise ValueError("Параметры операции не представимы корректным JSON") from None
        if len(payload) > MAX_FRAME_BYTES:
            raise ValueError("Запрос превышает предел 1 МиБ")
        deadline = time.monotonic() + duration
        if not self._request_lock.acquire(timeout=duration):
            raise NativeError("native_busy", "Истёк срок ожидания предыдущего нативного запроса", False, request_id)
        stream: socket.socket | None = None
        sent = False
        try:
            self._remaining(deadline, request_id, False)
            stream = self._connect(deadline, request_id)
            stream.settimeout(self._remaining(deadline, request_id, False))
            # Даже частичный sendall не позволяет обещать отсутствие исполнения.
            sent = True
            stream.sendall(struct.pack(">I", len(payload)) + payload)
            length = struct.unpack(">I", self._receive(stream, 4, deadline, request_id))[0]
            if not 0 < length <= MAX_FRAME_BYTES:
                raise NativeError("native_protocol_error", "Нативный кадр имеет недопустимый размер", True, request_id)
            return self._decode(self._receive(stream, length, deadline, request_id), request_id)
        except NativeError as error:
            if stream is not None and error.code in {"native_timeout", "native_disconnected", "native_protocol_error", "native_closed"}:
                self._drop(stream)
            raise
        except TimeoutError:
            if stream is not None:
                self._drop(stream)
            raise NativeError("native_timeout", "Истёк срок ожидания нативного ответа", sent, request_id) from None
        except OSError:
            if stream is not None:
                self._drop(stream)
            with self._state_lock:
                closed = self._closed
            if closed:
                raise self._closed_error(request_id, sent) from None
            raise NativeError("native_disconnected", "Нативный канал оборван", sent, request_id) from None
        finally:
            self._request_lock.release()

    def status(self, *, timeout: float | None = None) -> dict[str, Any]:
        """Запросить актуальный статус; не использовать кеш готовности."""
        return self.request("status", {}, timeout=timeout)

    def close(self):
        """Прекратить соединение, включая ожидающий ответ, и закрыть клиент."""
        with self._state_lock:
            self._closed = True
            stream = self._socket
            self._socket = None
        if stream is not None:
            self._drop(stream)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
