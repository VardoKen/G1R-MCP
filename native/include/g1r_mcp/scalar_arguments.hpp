#pragma once

#include <g1r_mcp/transport.hpp>
#include <cmath>
#include <algorithm>
#include <cstdint>
#include <limits>

namespace g1r_mcp {

inline int parameter_extent(int current, int offset, int size, int array_dim) {
    if (current<0 || current>4096 || offset<0 || size<1 || size>4096
        || offset>4096-size || array_dim!=1)
        throw HandlerError("unsupported_signature", "Некорректные отражённые параметры");
    return std::max(current,offset+size);
}

inline void validate_bool_layout(int size, std::uint8_t byte_offset, std::uint8_t mask) {
    if (size < 1 || byte_offset >= size || mask == 0)
        throw HandlerError("unsupported_property_layout", "BoolProperty выходит за пределы поля или имеет пустую маску");
}

inline std::int32_t int32_argument(const nlohmann::json& value) {
    if (!value.is_number_integer())
        throw HandlerError("invalid_argument", "Требуется целый int32 аргумент");
    if (value.is_number_unsigned()) {
        const auto integer = value.get<std::uint64_t>();
        if (integer > static_cast<std::uint64_t>(std::numeric_limits<std::int32_t>::max()))
            throw HandlerError("invalid_argument", "Аргумент вне int32");
        return static_cast<std::int32_t>(integer);
    }
    const auto integer = value.get<std::int64_t>();
    if (integer < std::numeric_limits<std::int32_t>::min()
        || integer > std::numeric_limits<std::int32_t>::max())
        throw HandlerError("invalid_argument", "Аргумент вне int32");
    return static_cast<std::int32_t>(integer);
}

inline double double_argument(const nlohmann::json& value) {
    if (!value.is_number()) throw HandlerError("invalid_argument", "Требуется числовой double аргумент");
    const auto converted = value.get<double>();
    if (!std::isfinite(converted))
        throw HandlerError("invalid_argument", "Требуется конечный double аргумент");
    return converted;
}

inline float float_argument(const nlohmann::json& value) {
    const auto converted = double_argument(value);
    constexpr auto maximum = static_cast<double>(std::numeric_limits<float>::max());
    if (converted > maximum || converted < -maximum)
        throw HandlerError("invalid_argument", "Аргумент вне конечного float диапазона");
    const auto result = static_cast<float>(converted);
    if (!std::isfinite(result))
        throw HandlerError("invalid_argument", "Требуется конечный float аргумент");
    return result;
}

} // namespace g1r_mcp
