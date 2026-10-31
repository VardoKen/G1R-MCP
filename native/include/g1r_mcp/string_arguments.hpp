#pragma once

#include <g1r_mcp/transport.hpp>
#include <cstddef>
#include <cstdint>
#include <string>

namespace g1r_mcp::string_arguments {

inline constexpr std::size_t max_utf16_units = 32767;

// Буфер служит только источником копирования для подтверждённого конструктора.
// Его указатель нельзя помещать непосредственно в игровой FString.
inline std::u16string encode(const nlohmann::json& value) {
    if (!value.is_string())
        throw HandlerError("invalid_argument", "FString требует строковый UTF-8 аргумент");
    const auto& input = value.get_ref<const std::string&>();
    if (input.size() > max_utf16_units * 3)
        throw HandlerError("invalid_argument", "FString превышает 32767 UTF-16 единиц");
    std::u16string result;
    result.reserve(input.size() < max_utf16_units ? input.size() : max_utf16_units);
    for (std::size_t offset = 0; offset < input.size();) {
        const auto first = static_cast<unsigned char>(input[offset++]);
        std::uint32_t scalar{};
        std::size_t continuation_count{};
        std::uint32_t minimum{};
        if (first < 0x80) {
            scalar = first;
        } else if (first >= 0xc2 && first <= 0xdf) {
            scalar = first & 0x1f;
            continuation_count = 1;
            minimum = 0x80;
        } else if (first >= 0xe0 && first <= 0xef) {
            scalar = first & 0x0f;
            continuation_count = 2;
            minimum = 0x800;
        } else if (first >= 0xf0 && first <= 0xf4) {
            scalar = first & 0x07;
            continuation_count = 3;
            minimum = 0x10000;
        }
        else throw HandlerError("invalid_argument", "FString содержит неверную последовательность UTF-8");
        if (continuation_count > input.size() - offset)
            throw HandlerError("invalid_argument", "FString содержит оборванную последовательность UTF-8");
        for (std::size_t i = 0; i < continuation_count; ++i) {
            const auto next = static_cast<unsigned char>(input[offset++]);
            if ((next & 0xc0) != 0x80)
                throw HandlerError("invalid_argument", "FString содержит неверную последовательность UTF-8");
            scalar = (scalar << 6) | (next & 0x3f);
        }
        if (scalar < minimum || scalar > 0x10ffff || (scalar >= 0xd800 && scalar <= 0xdfff))
            throw HandlerError("invalid_argument", "FString содержит недопустимое значение Unicode");
        if (!scalar) throw HandlerError("invalid_argument", "FString не принимает встроенный NUL");
        const auto units = scalar <= 0xffff ? 1u : 2u;
        if (result.size() > max_utf16_units - units)
            throw HandlerError("invalid_argument", "FString превышает 32767 UTF-16 единиц");
        if (units == 1) result.push_back(static_cast<char16_t>(scalar));
        else {
            scalar -= 0x10000;
            result.push_back(static_cast<char16_t>(0xd800 + (scalar >> 10)));
            result.push_back(static_cast<char16_t>(0xdc00 + (scalar & 0x3ff)));
        }
    }
    return result;
}

} // namespace g1r_mcp::string_arguments
