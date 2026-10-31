#pragma once

#include <g1r_mcp/transport.hpp>
#include <array>
#include <cstddef>
#include <span>

namespace g1r_mcp::aligned_parameter_buffer {

inline constexpr std::size_t capacity=4096;
inline constexpr std::size_t alignment=16;

// Память принадлежит host-коду: игровой allocator и деструкторы не используются.
class Buffer {
public:
    explicit Buffer(std::size_t extent):size_(extent) {
        if(extent>capacity)
            throw HandlerError("unsupported_signature", "Буфер параметров превышает 4096 байт");
    }

    unsigned char* data() noexcept {
        return reinterpret_cast<unsigned char*>(storage_.data());
    }
    const unsigned char* data() const noexcept {
        return reinterpret_cast<const unsigned char*>(storage_.data());
    }
    std::size_t size() const noexcept {return size_;}
    std::span<std::byte> bytes() noexcept {return {storage_.data(),size_};}
    std::span<const std::byte> bytes() const noexcept {return {storage_.data(),size_};}

private:
    // Обнуляется весь буфер, включая память за текущим extent и POD-padding.
    alignas(alignment) std::array<std::byte,capacity> storage_{};
    std::size_t size_{};
};

static_assert(alignof(Buffer)>=alignment);

} // namespace g1r_mcp::aligned_parameter_buffer
