#pragma once

#include <g1r_mcp/object_arguments.hpp>

namespace g1r_mcp {

// UClass участвует в двух разных отношениях: его собственный UObject-класс
// ограничен PropertyClass, а представляемый им класс ограничен MetaClass.
inline bool compatible_class_argument(std::uintptr_t represented_class,
                                      std::uintptr_t object_metaclass,
                                      std::uintptr_t property_class,
                                      std::uintptr_t meta_class,
                                      const std::function<std::uintptr_t(std::uintptr_t)>& superclass) {
    return compatible_object_class(object_metaclass,property_class,superclass)
        && compatible_object_class(represented_class,meta_class,superclass);
}

inline std::string class_argument_handle(const nlohmann::json& value) {
    try { return object_argument_handle(value); }
    catch(const HandlerError&) {
        throw HandlerError("invalid_class_argument","Классовый аргумент требует null или только {handle}");
    }
}

} // namespace g1r_mcp
