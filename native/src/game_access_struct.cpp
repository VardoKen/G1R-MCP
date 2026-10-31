#include "g1r_mcp/game_access.hpp"
#include "g1r_mcp/game_memory.hpp"
#include "g1r_mcp/struct_values.hpp"
#include <array>
#include <set>

namespace g1r_mcp {
using json=nlohmann::json;
using game_memory::read;
json GameAccess::read_struct(void* object,const std::string& handle,const std::string& property_name) {
    if(property_name.empty() || property_name.size()>256 || property_name.find('\0')!=std::string::npos)
        throw HandlerError("invalid_struct_params","Имя свойства требует 1..256 байтов без NUL");
    const auto initial=validate_object(object);
    struct ComponentMetadata {
        uintptr_t pointer{},next{},field_class{};
        uint64_t name{};
        struct_values::Component component;
        bool operator==(const ComponentMetadata&) const=default;
    };
    struct Metadata {
        uintptr_t field{},owner{},structure{},head{},super{},field_class{};
        int offset{},size{},dim{},owner_size{};
        uint64_t field_name{};
        Handle owner_identity,struct_identity;
        std::string path;
        struct_values::Layout layout;
        std::vector<ComponentMetadata> components;
    };
    auto capture=[&]() {
        Metadata result;
        auto klass=reinterpret_cast<uintptr_t>(validate_object(object).object_class);
        std::set<uintptr_t> classes,fields;
        int scanned=0;
        for(int depth=0;klass && !result.field;klass=read<uintptr_t>(klass+0x40),++depth) {
            if(depth>=64 || !classes.insert(klass).second)
                throw HandlerError("struct_scan_incomplete","Цепочка классов не завершена в пределах 64");
            validate_class(reinterpret_cast<void*>(klass));
            for(auto field=read<uintptr_t>(klass+0x50);field;field=read<uintptr_t>(field+0x18)) {
                if(++scanned>4096 || !fields.insert(field).second)
                    throw HandlerError("struct_scan_incomplete","Список свойств не завершён в пределах 4096");
                if(name(read<uint64_t>(field+0x20))==property_name){result.field=field;result.owner=klass;break;}
            }
        }
        if(!result.field)throw HandlerError("property_not_found","Свойство структуры не найдено");
        result.owner_identity=validate_object(reinterpret_cast<void*>(result.owner));
        result.field_name=read<uint64_t>(result.field+0x20);
        result.field_class=read<uintptr_t>(result.field+8);
        if(game_memory::field_type(result.field,[this](uint64_t value){return name(value);})!="StructProperty")
            throw HandlerError("unsupported_struct_type","Выбранное свойство не является точным StructProperty");
        result.offset=read<int32_t>(result.field+0x44);result.size=read<int32_t>(result.field+0x34);
        result.dim=read<int32_t>(result.field+0x30);result.owner_size=read<int32_t>(result.owner+0x58);
        if(result.dim!=1 || result.offset<0 || (result.size!=12 && result.size!=24)
           || result.owner_size<result.size || result.offset>result.owner_size-result.size)
            throw HandlerError("unsupported_struct_layout","Внешнее свойство структуры не поддержано");
        result.structure=read<uintptr_t>(result.field+0x70);
        result.struct_identity=validate_object(reinterpret_cast<void*>(result.structure));
        validate_class(result.struct_identity.object_class);
        result.path=object_path(reinterpret_cast<void*>(result.structure));
        if(result.path.empty() || result.path.size()>8192)
            throw HandlerError("struct_identity_too_large","Путь структуры не подтверждён в пределах 8 КиБ");
        result.head=read<uintptr_t>(result.structure+0x50);result.super=read<uintptr_t>(result.structure+0x40);
        result.layout.properties_size=read<int32_t>(result.structure+0x58);
        result.layout.min_alignment=read<int32_t>(result.structure+0x5c);
        result.layout.has_super=result.super!=0;
        if(result.layout.properties_size!=result.size)
            throw HandlerError("unsupported_struct_layout","Размер Struct не совпадает с внешним свойством");
        std::set<uintptr_t> children;
        for(auto field=result.head;field;field=read<uintptr_t>(field+0x18)) {
            if(children.size()>=3 || !children.insert(field).second)
                throw HandlerError("unsupported_struct_layout","Структура содержит лишние поля или цикл");
            ComponentMetadata child;
            child.pointer=field;child.next=read<uintptr_t>(field+0x18);child.field_class=read<uintptr_t>(field+8);
            child.name=read<uint64_t>(field+0x20);
            child.component={name(child.name),game_memory::field_type(field,[this](uint64_t value){return name(value);}),
                             read<int32_t>(field+0x34),read<int32_t>(field+0x44),read<int32_t>(field+0x30)};
            result.layout.components.push_back(child.component);result.components.push_back(std::move(child));
        }
        struct_values::validate_layout(result.layout);
        return result;
    };
    const auto before=capture();
    std::array<std::byte,24> bytes{};
    const auto address=reinterpret_cast<uintptr_t>(object)+static_cast<uintptr_t>(before.offset);
    if(address<reinterpret_cast<uintptr_t>(object)
       || !game_memory::copy(bytes.data(),reinterpret_cast<void*>(address),static_cast<size_t>(before.size)))
        throw HandlerError("invalid_memory","Байты структуры недоступны");
    const auto components=struct_values::copied_components(before.layout,std::span(bytes).first(before.size));
    const auto after=capture();
    if(before.field!=after.field || before.owner!=after.owner || before.structure!=after.structure
       || before.head!=after.head || before.super!=after.super || before.field_class!=after.field_class
       || before.offset!=after.offset || before.size!=after.size || before.dim!=after.dim
       || before.owner_size!=after.owner_size || before.field_name!=after.field_name
       || before.path!=after.path || !(before.layout==after.layout) || before.components!=after.components
       || !same_serial_identity(serial_identity(before.owner_identity),serial_identity(after.owner_identity))
       || !same_serial_identity(serial_identity(before.struct_identity),serial_identity(after.struct_identity))
       || !same_serial_identity(serial_identity(initial),serial_identity(validate_object(object))))
        throw HandlerError("struct_metadata_changed","Метаданные или identity структуры изменились при копировании");
    if(resolve(handle)!=object)throw HandlerError("stale_handle","Ссылка структуры изменилась");
    return {{"source","live_native"},{"session_id",handle_session_},{"handle",handle},{"property_name",property_name},
            {"struct_path",before.path},{"properties_size",before.layout.properties_size},
            {"min_alignment",before.layout.min_alignment},{"components",components},
            {"consistency","copied_bytes_then_metadata_revalidated"},{"partial",false},{"truncated",false}};
}
} // namespace g1r_mcp
