#include "g1r_mcp/game_access.hpp"
#include "g1r_mcp/game_memory.hpp"
#include "g1r_mcp/scalar_struct_values.hpp"
#include <set>
#include <vector>
namespace g1r_mcp {
using json=nlohmann::json;
using game_memory::read;
json GameAccess::read_struct(void* object,const std::string& handle,const std::string& property_name) {
    if(property_name.empty() || property_name.find('\0')!=std::string::npos)
        throw HandlerError("invalid_struct_params","property_name must be nonempty and contain no NUL");
    const auto initial=validate_object(object);
    struct Metadata {
        uintptr_t owner{};
        int owner_size{};
        Handle owner_identity;
        scalar_struct_values::Snapshot structure;
    };
    auto capture=[&]() {
        Metadata result;
        uintptr_t selected{};
        auto klass=reinterpret_cast<uintptr_t>(validate_object(object).object_class);
        std::set<uintptr_t> classes,fields;
        for(;klass && !selected;klass=read<uintptr_t>(klass+0x40)) {
            if(!classes.insert(klass).second)
                throw HandlerError("struct_scan_incomplete","Class chain is cyclic");
            validate_class(reinterpret_cast<void*>(klass));
            for(auto field=read<uintptr_t>(klass+0x50);field;field=read<uintptr_t>(field+0x18)) {
                if(!fields.insert(field).second)
                    throw HandlerError("struct_scan_incomplete","Property chain is cyclic");
                if(name(read<uint64_t>(field+0x20))==property_name){selected=field;result.owner=klass;break;}
            }
        }
        if(!selected)throw HandlerError("property_not_found","Свойство структуры не найдено");
        result.owner_identity=validate_object(reinterpret_cast<void*>(result.owner));
        if(game_memory::field_type(selected,[this](uint64_t value){return name(value);})!="StructProperty")
            throw HandlerError("unsupported_struct_type","Выбранное свойство не является точным StructProperty");
        const auto size=read<int32_t>(selected+0x34),dim=read<int32_t>(selected+0x30);
        result.owner_size=read<int32_t>(result.owner+0x58);
        if(dim!=1 || size<1)
            throw HandlerError("unsupported_struct_layout","Внешнее свойство структуры не поддержано");
        result.structure=scalar_struct_values::capture(selected,size,dim,
            []<class Value>(uintptr_t address){return read<Value>(address);},
            [this](uint64_t value){return name(value);},
            [this](uintptr_t value){return serial_identity(validate_object(reinterpret_cast<void*>(value)));},
            [this](uintptr_t value){validate_class(reinterpret_cast<void*>(value));},
            [this](uintptr_t value){return object_path(reinterpret_cast<void*>(value));});
        if(result.structure.offset<0 || result.owner_size<size || result.structure.offset>result.owner_size-size)
            throw HandlerError("unsupported_struct_layout","Внешнее свойство структуры не поддержано");
        return result;
    };
    const auto before=capture();
    std::vector<std::byte> bytes(static_cast<size_t>(before.structure.element_size));
    const auto address=reinterpret_cast<uintptr_t>(object)+static_cast<uintptr_t>(before.structure.offset);
    if(address<reinterpret_cast<uintptr_t>(object)
       || !game_memory::copy(bytes.data(),reinterpret_cast<void*>(address),bytes.size()))
        throw HandlerError("invalid_memory","Байты структуры недоступны");
    const auto values=scalar_struct_values::decode(before.structure.layout,bytes);
    json components=json::array();
    for(const auto& component:before.structure.layout.components)
        components.push_back({{"name",component.name},{"type",component.layout.type},
            {"element_size",component.layout.element_size},{"offset",component.offset},{"value",values.at(component.name)}});
    const auto after=capture();
    if(before.owner!=after.owner || before.owner_size!=after.owner_size || !(before.structure==after.structure)
       || !same_serial_identity(serial_identity(before.owner_identity),serial_identity(after.owner_identity))
       || !same_serial_identity(serial_identity(initial),serial_identity(validate_object(object))))
        throw HandlerError("struct_metadata_changed","Метаданные или identity структуры изменились при копировании");
    if(resolve(handle)!=object)throw HandlerError("stale_handle","Ссылка структуры изменилась");
    return {{"source","live_native"},{"session_id",handle_session_},{"handle",handle},{"property_name",property_name},
            {"struct_path",before.structure.path},{"properties_size",before.structure.layout.properties_size},
            {"min_alignment",before.structure.layout.min_alignment},{"components",components},
            {"consistency","copied_bytes_then_metadata_revalidated"},{"partial",false},{"truncated",false}};
}
} // namespace g1r_mcp
