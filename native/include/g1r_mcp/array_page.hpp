#pragma once

#include <g1r_mcp/transport.hpp>
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <limits>

namespace g1r_mcp {

struct ArrayHeader {
    std::uintptr_t data{};
    std::int32_t count{};
    std::int32_t capacity{};
};
static_assert(sizeof(ArrayHeader)==16);
inline constexpr std::int32_t MaxArrayElements=1000000;
inline constexpr std::size_t MaxArrayPageBytes=64*1024;

struct ArrayPage {
    std::uintptr_t begin{};
    std::size_t bytes{};
    std::int32_t offset{};
    std::int32_t count{};
    std::int32_t next_offset{};
    bool has_more{};
};

inline bool same_array_header(const ArrayHeader& left,const ArrayHeader& right) {
    return left.data==right.data && left.count==right.count && left.capacity==right.capacity;
}

inline ArrayPage array_page(const ArrayHeader& header,int stride,int array_dim,
                            std::int32_t offset,int limit) {
    if(stride<1 || stride>static_cast<int>(MaxArrayPageBytes) || array_dim!=1)
        throw HandlerError("unsupported_array_layout","Неподдерживаемые stride или ArrayDim элемента");
    if(header.count<0 || header.capacity<header.count || header.capacity>MaxArrayElements
        || header.count>MaxArrayElements || (!header.data && (header.count || header.capacity)))
        throw HandlerError("invalid_array_header","Некорректные размеры или указатель массива");
    if(header.data && header.capacity==0)
        throw HandlerError("invalid_array_header","Ненулевой указатель массива без ёмкости");
    if(offset<0 || offset>header.count || limit<1 || limit>200)
        throw HandlerError("invalid_array_page","Страница требует offset внутри массива и limit от 1 до 200");
    const auto unsigned_stride=static_cast<std::uintptr_t>(stride);
    const auto capacity_bytes=static_cast<std::uintptr_t>(header.capacity)*unsigned_stride;
    if(header.data>std::numeric_limits<std::uintptr_t>::max()-capacity_bytes)
        throw HandlerError("invalid_array_header","Адресный диапазон массива переполнен");
    const auto count=std::min<std::int32_t>(header.count-offset,limit);
    const auto bytes=static_cast<std::size_t>(count)*static_cast<std::size_t>(stride);
    if(bytes>MaxArrayPageBytes)
        throw HandlerError("array_page_too_large","Страница массива превышает 64 КиБ");
    const auto begin=header.data+static_cast<std::uintptr_t>(offset)*unsigned_stride;
    return {begin,bytes,offset,count,offset+count,offset+count<header.count};
}

} // namespace g1r_mcp
