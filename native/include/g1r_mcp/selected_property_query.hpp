#pragma once

#include <g1r_mcp/transport.hpp>

#include <functional>
#include <string>
#include <unordered_set>
#include <utility>

namespace g1r_mcp::selected_property_query {

inline constexpr std::size_t max_properties = 32;
inline constexpr std::size_t max_name_bytes = 256;

inline void validate(const nlohmann::json& names) {
    if(!names.is_array() || names.empty() || names.size()>max_properties)
        throw HandlerError("invalid_property_names", "property_names требует от 1 до 32 имён свойств");
    std::unordered_set<std::string> unique;
    for(const auto& value:names) {
        if(!value.is_string())
            throw HandlerError("invalid_property_names", "Каждое имя свойства должно быть строкой");
        const auto& name=value.get_ref<const std::string&>();
        if(name.empty() || name.size()>max_name_bytes || name.find('\0')!=std::string::npos)
            throw HandlerError("invalid_property_names", "Имя свойства требует 1–256 байт без NUL");
        if(!unique.insert(name).second)
            throw HandlerError("invalid_property_names", "Имена свойств не должны повторяться");
    }
}

// Проверка запроса завершается до reader. Имена не нормализуются и не переставляются.
template<class Reader>
auto validated(const nlohmann::json& names,Reader&& reader) {
    validate(names);
    return std::invoke(std::forward<Reader>(reader),names);
}

} // namespace g1r_mcp::selected_property_query
