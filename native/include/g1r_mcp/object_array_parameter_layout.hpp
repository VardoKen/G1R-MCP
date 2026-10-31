#pragma once

#include <g1r_mcp/object_serial.hpp>
#include <g1r_mcp/parameter_direction.hpp>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <limits>
#include <string>
#include <utility>

namespace g1r_mcp::object_array_parameter_layout {

inline std::uintptr_t checked_address(std::uintptr_t base,std::size_t offset,std::size_t bytes) {
    if(!base || offset>std::numeric_limits<std::uintptr_t>::max()-base)
        throw HandlerError("invalid_memory","Адрес метаданных массива UObject переполняется");
    const auto address=base+offset;
    if(bytes && bytes-1>std::numeric_limits<std::uintptr_t>::max()-address)
        throw HandlerError("invalid_memory","Диапазон метаданных массива UObject переполняется");
    return address;
}

inline nlohmann::json identity_json(const ObjectSerialIdentity& value) {
    return {{"object",value.object},{"object_class",value.object_class},{"index",value.index},
        {"name",value.name},{"serial",value.serial},{"class_index",value.class_index},
        {"class_name",value.class_name},{"class_serial",value.class_serial}};
}

// Снимок не читает элементы массива и не выделяет игровую память.
// Параметр допускает только const-ссылку на TArray с точным ObjectProperty.
template<class Read,class NameDecoder,class ObjectCapture,class ClassValidator,class PathResolver>
nlohmann::json capture(std::uintptr_t field,int element_size,int array_dim,std::uint64_t expected_flags,
    Read&& read,NameDecoder&& decode,ObjectCapture&& capture_identity,
    ClassValidator&& validate_class,PathResolver&& object_path) {
    using namespace parameter_direction;
    if(element_size!=16 || array_dim!=1)
        throw HandlerError("unsupported_object_array_layout","Массив UObject требует размер 16 и ArrayDim=1");
    const auto pointer=[&](std::uintptr_t base,std::size_t offset) {
        return read.template operator()<std::uintptr_t>(checked_address(base,offset,sizeof(std::uintptr_t)));
    };
    const auto name_value=[&](std::uintptr_t base,std::size_t offset) {
        return read.template operator()<std::uint64_t>(checked_address(base,offset,sizeof(std::uint64_t)));
    };
    const auto integer=[&](std::uintptr_t base,std::size_t offset) {
        return read.template operator()<std::int32_t>(checked_address(base,offset,sizeof(std::int32_t)));
    };
    const auto field_snapshot=[&](std::uintptr_t current) {
        const auto field_class=pointer(current,8);
        const auto type_name=name_value(field_class,0);
        const auto raw_name=name_value(current,0x20);
        return nlohmann::json{{"pointer",current},{"next",pointer(current,0x18)},
            {"owner",name_value(current,0x10)},{"field_class",field_class},{"type_name",type_name},
            {"type",std::invoke(decode,type_name)},{"name",raw_name},{"name_text",std::invoke(decode,raw_name)},
            {"field_flags",integer(current,0x28)},{"element_size",integer(current,0x34)},
            {"array_dim",integer(current,0x30)},{"offset",integer(current,0x44)},
            {"flags",name_value(current,0x38)}};
    };

    auto outer=field_snapshot(field);
    const auto flags=outer.at("flags").template get<std::uint64_t>();
    const auto offset=outer.at("offset").template get<std::int32_t>();
    if(outer.at("type")!="ArrayProperty")
        throw HandlerError("unsupported_object_array_type","Параметр должен быть точным ArrayProperty");
    if(outer.at("element_size")!=element_size || outer.at("array_dim")!=array_dim
       || offset<0 || offset%8)
        throw HandlerError("unsupported_object_array_layout","Внешняя раскладка массива UObject не совпадает");
    if(flags!=expected_flags || !(flags&parm) || !(flags&const_parm)
       || !(flags&(reference_parm|out_parm)) || (flags&return_parm))
        throw HandlerError("unsupported_signature","Массив UObject допускает только const ref/in параметр");
    const auto array_flags=read.template operator()<std::uint8_t>(checked_address(field,0x70,1));
    outer["array_flags"]=array_flags;
    if(array_flags)
        throw HandlerError("unsupported_object_array_layout","Неизвестные ArrayFlags массива UObject");
    const auto inner_pointer=pointer(field,0x78);
    outer["inner"]=inner_pointer;
    if(!inner_pointer || inner_pointer==field || outer.at("next")==field)
        throw HandlerError("unsupported_object_array_layout","Inner массива UObject отсутствует или содержит цикл");
    auto inner=field_snapshot(inner_pointer);
    if(inner.at("next")==inner_pointer || inner.at("next")==field)
        throw HandlerError("unsupported_object_array_layout","Inner массива UObject содержит цикл метаданных");
    if(inner.at("type")!="ObjectProperty")
        throw HandlerError("unsupported_object_array_type","Элементы массива требуют точный ObjectProperty");
    if(inner.at("element_size")!=8 || inner.at("array_dim")!=1 || inner.at("offset")!=0)
        throw HandlerError("unsupported_object_array_layout","Inner массива UObject требует размер 8, ArrayDim=1 и offset=0");
    const auto property_class=pointer(inner_pointer,0x70);
    inner["property_class"]=property_class;
    std::invoke(validate_class,property_class);
    const auto identity=std::invoke(capture_identity,property_class);
    if(identity.object!=property_class || !identity.object_class || !same_serial_identity(identity,identity))
        throw HandlerError("unsupported_object_array_type","Identity класса элементов массива UObject не совпадает");
    std::invoke(validate_class,identity.object_class);
    const auto path=std::invoke(object_path,property_class);
    const auto class_name=std::invoke(decode,identity.name);
    if(path.empty() || path.front()!='/' || class_name.empty())
        throw HandlerError("unsupported_object_array_type","Класс элементов массива UObject не имеет подтверждённого пути");
    return {{"outer",std::move(outer)},{"inner",std::move(inner)},{"property_class",property_class},
        {"property_class_identity",identity_json(identity)},{"property_class_path",path},{"property_class_name",class_name},
        {"public",{{"inner_type","ObjectProperty"},{"inner_element_size",8},{"inner_array_dim",1},{"inner_offset",0},
            {"property_class",{{"path",path},{"name",class_name}}}}}};
}

} // namespace g1r_mcp::object_array_parameter_layout
