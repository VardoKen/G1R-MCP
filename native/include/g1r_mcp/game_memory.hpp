#pragma once

#include <g1r_mcp/transport.hpp>
#include <Windows.h>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <functional>
#include <type_traits>
#include <utility>

namespace g1r_mcp::game_memory {

// SEH оборачивает только копирование байтов: здесь нет объектов с C++ unwind.
inline bool copy(void* destination,const void* source,std::size_t bytes) noexcept {
    __try {std::memcpy(destination,source,bytes);return true;}
    __except(EXCEPTION_EXECUTE_HANDLER){return false;}
}

template<class Value>Value read(std::uintptr_t address) {
    static_assert(std::is_trivially_copyable_v<Value>);
    Value value{};
    if(!copy(&value,reinterpret_cast<const void*>(address),sizeof(Value)))
        throw HandlerError("invalid_memory","Игровая память недоступна");
    return value;
}

template<class NameDecoder>
std::string field_type(std::uintptr_t field,NameDecoder&& decode_name) {
    const auto field_class=read<std::uintptr_t>(field+8);
    return std::invoke(std::forward<NameDecoder>(decode_name),read<std::uint64_t>(field_class));
}

} // namespace g1r_mcp::game_memory
