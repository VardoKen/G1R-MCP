#pragma once

#include <g1r_mcp/transport.hpp>
#include <cstdint>
#include <functional>
#include <set>

namespace g1r_mcp {

struct ObjectIdentity {
    std::uintptr_t object{};
    std::uintptr_t object_class{};
    std::int32_t index{};
    std::uint64_t name{};
};
inline bool same_object_identity(const ObjectIdentity& left,const ObjectIdentity& right) {
    return left.object==right.object && left.object_class==right.object_class
        && left.index==right.index && left.name==right.name;
}

// Пустая строка обозначает явный JSON null. Сам указатель никогда не является
// внешним аргументом: только непрозрачная ссылка текущей транспортной сессии.
inline std::string object_argument_handle(const nlohmann::json& value) {
    if (value.is_null()) return {};
    if (!value.is_object() || value.size()!=1 || !value.contains("handle")
        || !value.at("handle").is_string() || value.at("handle").get_ref<const std::string&>().empty()
        || value.at("handle").get_ref<const std::string&>().size()>80)
        throw HandlerError("invalid_object_argument", "Объектный аргумент требует null или только {handle}");
    return value.at("handle").get<std::string>();
}

inline bool compatible_object_class(std::uintptr_t actual, std::uintptr_t expected,
                                    const std::function<std::uintptr_t(std::uintptr_t)>& superclass) {
    if (!actual || !expected) throw HandlerError("invalid_object_class", "Класс объекта отсутствует");
    std::set<std::uintptr_t> visited;
    while (actual) {
        if (!visited.insert(actual).second)
            throw HandlerError("invalid_object_class", "Цикл наследования класса объекта");
        if (actual==expected) return true;
        actual=superclass(actual);
    }
    return false;
}

} // namespace g1r_mcp
