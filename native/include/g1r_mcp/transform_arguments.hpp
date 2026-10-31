#pragma once

#include <g1r_mcp/scalar_arguments.hpp>
#include <g1r_mcp/transform_layout.hpp>
#include <g1r_mcp/vector_arguments.hpp>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstring>
#include <initializer_list>
#include <span>
#include <string>
#include <utility>

namespace g1r_mcp::transform_arguments {

// Ограничение инструмента; не является утверждением о пороге Unreal IsNormalized.
inline constexpr double unit_quaternion_tolerance=1e-6;

struct EncodedTransform {
    std::array<std::byte,96> bytes{};
    int size{96};
};

inline void validate_keys(const nlohmann::json& value,
    std::initializer_list<const char*> keys,const char* message) {
    if(!value.is_object() || value.size()!=keys.size())
        throw HandlerError("invalid_argument",message);
    for(const auto* key:keys)
        if(!value.contains(key))throw HandlerError("invalid_argument",message);
}

inline EncodedTransform encode(const transform_layout::Layout& layout,
    const nlohmann::json& value) {
    // Identity UScriptStruct и повторную проверку reflection выполняет вызывающий код.
    transform_layout::validate_layout(layout);
    validate_keys(value,{"Rotation","Translation","Scale3D"},
        "FTransform требует только Rotation, Translation и Scale3D");
    const auto& rotation=value.at("Rotation");
    validate_keys(rotation,{"X","Y","Z","W"},
        "Rotation требует только {X:number,Y:number,Z:number,W:number}");
    validate_keys(value.at("Translation"),{"X","Y","Z"},
        "Translation требует только {X:number,Y:number,Z:number}");
    validate_keys(value.at("Scale3D"),{"X","Y","Z"},
        "Scale3D требует только {X:number,Y:number,Z:number}");

    constexpr std::array<const char*,4> names{"X","Y","Z","W"};
    std::array<double,4> components{};
    double squared_length{};
    for(std::size_t i=0;i<names.size();++i) {
        components[i]=double_argument(rotation.at(names[i]));
        // Проверка до умножения исключает overflow при подсчёте нормы.
        if(std::abs(components[i])>1.0+unit_quaternion_tolerance)
            throw HandlerError("invalid_argument", "Rotation должен быть единичным quaternion без нормализации");
        squared_length+=components[i]*components[i];
    }
    if(std::abs(squared_length-1.0)>unit_quaternion_tolerance)
        throw HandlerError("invalid_argument", "Rotation должен быть единичным quaternion без нормализации");

    const auto translation=vector_arguments::encode(layout.translation,value.at("Translation"));
    const auto scale=vector_arguments::encode(layout.scale3d,value.at("Scale3D"));
    EncodedTransform result;
    for(const auto& component:layout.rotation.components) {
        for(std::size_t i=0;i<names.size();++i) {
            if(component.name!=names[i])continue;
            std::memcpy(result.bytes.data()+component.offset,&components[i],sizeof(double));
            break;
        }
    }
    std::memcpy(result.bytes.data()+32,translation.bytes.data(),translation.size);
    std::memcpy(result.bytes.data()+64,scale.bytes.data(),scale.size);
    // Padding 56..63 и 88..95 остаётся нулевым; значения quaternion не изменяются.
    return result;
}

inline nlohmann::json decode(const transform_layout::Layout& layout,std::span<const std::byte> bytes) {
    transform_layout::validate_layout(layout);
    if(bytes.size()!=static_cast<std::size_t>(layout.properties_size))
        throw HandlerError("invalid_struct_copy","Размер копии Transform не совпадает с отражённой раскладкой");
    auto rotation=nlohmann::json::object();
    for(const auto& component:layout.rotation.components) {
        double value{};
        std::memcpy(&value,bytes.data()+component.offset,sizeof(value));
        if(!std::isfinite(value))
            throw HandlerError("invalid_struct_value","Компонент quaternion не является конечным числом");
        rotation[component.name]=value;
    }
    return {{"Rotation",std::move(rotation)},
        {"Translation",vector_arguments::decode(layout.translation,bytes.subspan(32,24))},
        {"Scale3D",vector_arguments::decode(layout.scale3d,bytes.subspan(64,24))}};
}

} // namespace g1r_mcp::transform_arguments
