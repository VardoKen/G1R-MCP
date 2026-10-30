"""Ограниченный кодек диагностического протокола AngelScript-отладчика.

Независимая реализация по формату открытого клиента Hazelight, MIT,
Copyright 2019 Hazelight Games AB; исходный код не копировался.
Ревизия: ffc7ab2948589abb04200b7dcedd326eaeaf7a4d.
https://github.com/Hazelight/vscode-unreal-angelscript/blob/ffc7ab2948589abb04200b7dcedd326eaeaf7a4d/extension/src/unreal-debugclient.ts
https://github.com/Hazelight/vscode-unreal-angelscript/blob/ffc7ab2948589abb04200b7dcedd326eaeaf7a4d/extension/src/debug.ts

Заголовки направлений различаются: клиентская длина включает type,
серверная — только payload. Совместимость с G1R требует отдельной пробы.
"""

from dataclasses import dataclass
from enum import IntEnum
import struct


class ProtocolError(ValueError):
    """Сообщение нарушает проверяемый формат или диагностический предел."""


class MessageType(IntEnum):
    DIAGNOSTICS = 0
    REQUEST_DEBUG_DATABASE = 1
    DEBUG_DATABASE = 2
    START_DEBUGGING = 3
    REQUEST_EVALUATE = 19
    EVALUATE = 20
    DISCONNECT = 25
    DEBUG_DATABASE_FINISHED = 26
    DEBUG_SERVER_VERSION = 33


@dataclass(frozen=True)
class Frame:
    """Серверное сообщение без предположений о неизвестном payload."""

    message_type: int
    payload: bytes


def encode_client_frame(message_type: int, payload: bytes = b"") -> bytes:
    """Собрать исходящий кадр: длина включает однобайтовый тип."""
    if not 0 <= message_type <= 255:
        raise ProtocolError("Тип сообщения должен помещаться в один байт")
    if len(payload) > 0xFFFFFFFE:
        raise ProtocolError("Исходящий payload превышает формат uint32")
    return struct.pack("<IB", len(payload) + 1, message_type) + payload


def encode_ascii_string(value: str) -> bytes:
    """Записать подтверждённый исходящий ASCII FString с конечным NUL."""
    if "\0" in value:
        raise ProtocolError("Исходящая строка содержит встроенный NUL")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ProtocolError("Исходящий Unicode-формат не подтверждён; допустим ASCII") from error
    if len(encoded) >= 0x7FFFFFFF:
        raise ProtocolError("Исходящая строка превышает формат int32")
    return struct.pack("<i", len(encoded) + 1) + encoded + b"\0"


def encode_evaluate(expression: str, frame_id: int) -> bytes:
    """Собрать один запрос Evaluate; frame_id не имеет доказанного default."""
    if not -0x80000000 <= frame_id <= 0x7FFFFFFF:
        raise ProtocolError("frame_id должен помещаться в int32")
    return encode_client_frame(
        MessageType.REQUEST_EVALUATE,
        encode_ascii_string(expression) + struct.pack("<i", frame_id),
    )


class ServerFrameDecoder:
    """Пошаговое чтение серверных кадров с пределом одного payload."""

    def __init__(self, max_payload_bytes: int):
        if not 1 <= max_payload_bytes <= 0xFFFFFFFF:
            raise ValueError("Предел payload должен быть положительным uint32")
        self.max_payload_bytes = max_payload_bytes
        self._buffer = bytearray()

    @property
    def pending_bytes(self) -> int:
        return len(self._buffer)

    def feed(self, data: bytes) -> list[Frame]:
        """Потребить фрагмент; несколько полных кадров могут идти подряд."""
        frames = []
        offset = 0
        while offset < len(data):
            if len(self._buffer) < 5:
                needed = 5 - len(self._buffer)
            else:
                payload_length = struct.unpack_from("<I", self._buffer)[0]
                if payload_length > self.max_payload_bytes:
                    raise ProtocolError("Серверный payload превышает диагностический предел")
                needed = 5 + payload_length - len(self._buffer)
            take = min(needed, len(data) - offset)
            self._buffer.extend(data[offset:offset + take])
            offset += take
            if len(self._buffer) < 5:
                continue
            payload_length, message_type = struct.unpack_from("<IB", self._buffer)
            if payload_length > self.max_payload_bytes:
                raise ProtocolError("Серверный payload превышает диагностический предел")
            if len(self._buffer) == 5 + payload_length:
                frames.append(Frame(message_type, bytes(self._buffer[5:])))
                self._buffer.clear()
        return frames

    def finish(self) -> None:
        """Подтвердить отсутствие неполного кадра при закрытии/timeout."""
        if self._buffer:
            raise ProtocolError("Получен незавершённый серверный кадр")


class PayloadReader:
    """Проверяемые int32 и FString внутри уже ограниченного payload."""

    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0

    def _take(self, count: int) -> bytes:
        if count > len(self.payload) - self.offset:
            raise ProtocolError("Payload обрезан при чтении значения")
        value = self.payload[self.offset:self.offset + count]
        self.offset += count
        return value

    def read_int(self) -> int:
        return struct.unpack("<i", self._take(4))[0]

    def read_string(self) -> str:
        count = self.read_int()
        if count == -0x80000000:
            raise ProtocolError("Некорректная длина FString")
        raw = self._take(-count * 2 if count < 0 else count)
        try:
            value = raw.decode("utf-16le" if count < 0 else "utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise ProtocolError("FString содержит некорректную кодировку") from error
        # Upstream снимает конечный NUL, но не требует его наличия.
        return value[:-1] if value.endswith("\0") else value

    def finish(self) -> None:
        if self.offset != len(self.payload):
            raise ProtocolError("В известном payload остались лишние байты")


def decode_server_version(payload: bytes) -> int:
    reader = PayloadReader(payload)
    version = reader.read_int()
    reader.finish()
    return version


def decode_evaluate(payload: bytes) -> dict:
    """Ответ не имеет request_id; принадлежность определяется единственным запросом."""
    reader = PayloadReader(payload)
    result = {
        "name": reader.read_string(),
        "value": reader.read_string(),
        "type": reader.read_string(),
        "has_members": reader.read_int() != 0,
    }
    reader.finish()
    return result
