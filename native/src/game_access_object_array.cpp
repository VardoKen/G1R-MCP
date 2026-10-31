#include "g1r_mcp/game_access.hpp"
#include "g1r_mcp/game_memory.hpp"
#include "g1r_mcp/object_array_parameter_layout.hpp"

namespace g1r_mcp {

nlohmann::json GameAccess::capture_object_array_parameter(std::uintptr_t field,int size,int dim,std::uint64_t flags) const {
    const auto capture=[&] {
        return object_array_parameter_layout::capture(field,size,dim,flags,
            []<class Value>(std::uintptr_t address) {return game_memory::read<Value>(address);},
            [this](std::uint64_t value) {return name(value);},
            [this](std::uintptr_t object) {return serial_identity(validate_object(reinterpret_cast<void*>(object)));},
            [this](std::uintptr_t object_class) {validate_class(reinterpret_cast<void*>(object_class));},
            [this](std::uintptr_t object) {return object_path(reinterpret_cast<void*>(object));});
    };
    const auto before=capture();
    const auto after=capture();
    if(before!=after)
        throw HandlerError("object_array_metadata_changed","Отражённые метаданные массива UObject изменились при проверке");
    return before;
}

} // namespace g1r_mcp
