#pragma once

#include <g1r_mcp/transport.hpp>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <span>
#include <string>

namespace g1r_mcp::scalar_outputs {

// POD-поле из копии параметров ProcessEvent. Проверка CPF и offset — у вызывающего кода.
struct Layout {
    std::string type;
    int element_size{};
    int array_dim{};
    std::uint8_t field_size{},byte_offset{},byte_mask{},field_mask{};
    bool operator==(const Layout&) const=default;
};

inline void validate_layout(const Layout& layout) {
    if(layout.array_dim!=1)
        throw HandlerError("unsupported_scalar_output_layout","Скалярный out требует ArrayDim=1");
    if(layout.type=="BoolProperty") {
        const auto mask=layout.field_mask;
        const bool byte_mask_valid=layout.byte_mask!=0
            && (layout.byte_mask==0xff || !(layout.byte_mask&(layout.byte_mask-1)));
        // Native bool читает весь байт (FieldMask=0xff), а setter может писать только ByteMask=1.
        // Для битового bool сохраняется точная одинаковая одиночная маска чтения/записи.
        const bool masks_valid=mask==0xff?byte_mask_valid:
            mask!=0 && !(mask&(mask-1)) && layout.byte_mask==mask;
        if(layout.element_size!=1 || layout.field_size!=1 || layout.byte_offset!=0
           || !masks_valid)
            throw HandlerError("unsupported_scalar_output_layout","Bool out требует один байт и подтверждённую маску");
        return;
    }
    const int expected=(layout.type=="IntProperty" || layout.type=="UInt32Property" || layout.type=="FloatProperty")?4:
        (layout.type=="Int64Property" || layout.type=="UInt64Property" || layout.type=="DoubleProperty")?8:0;
    if(expected==0 || layout.element_size!=expected)
        throw HandlerError("unsupported_scalar_output_layout","Тип или размер скалярного out не поддержан");
}

template<class T>inline T copied_value(std::span<const std::byte> bytes) {
    T value{};
    std::memcpy(&value,bytes.data(),sizeof(value));
    return value;
}

inline nlohmann::json decode(const Layout& layout,std::span<const std::byte> bytes) {
    scalar_outputs::validate_layout(layout);
    if(bytes.size()!=static_cast<std::size_t>(layout.element_size))
        throw HandlerError("invalid_scalar_copy","Размер копии скалярного out не совпадает с reflection");
    if(layout.type=="BoolProperty")
        return (copied_value<std::uint8_t>(bytes)&layout.field_mask)!=0;
    if(layout.type=="IntProperty")return copied_value<std::int32_t>(bytes);
    if(layout.type=="Int64Property")return copied_value<std::int64_t>(bytes);
    if(layout.type=="UInt32Property")return copied_value<std::uint32_t>(bytes);
    if(layout.type=="UInt64Property")return copied_value<std::uint64_t>(bytes);
    const double value=layout.type=="FloatProperty"?static_cast<double>(copied_value<float>(bytes)):copied_value<double>(bytes);
    if(!std::isfinite(value))
        throw HandlerError("invalid_scalar_value","Скалярный out содержит NaN или Infinity");
    return value;
}

} // namespace g1r_mcp::scalar_outputs
