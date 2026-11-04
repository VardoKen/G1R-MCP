#include "g1r_mcp/game_access.hpp"
#include "g1r_mcp/game_memory.hpp"
#include "g1r_mcp/object_array_parameter_layout.hpp"
#include "g1r_mcp/vector_parameter_direction.hpp"

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
        throw HandlerError("object_array_metadata_changed","Reflected UObject array metadata changed during validation");
    return before;
}

nlohmann::json GameAccess::capture_int32_array_parameter(std::uintptr_t field,int size,int dim,std::uint64_t flags) const {
    using json=nlohmann::json;
    using game_memory::read;
    const auto capture=[&]() {
        const auto field_snapshot=[&](std::uintptr_t pointer) {
            const auto field_class=read<std::uintptr_t>(pointer+8);
            const auto type_name=read<std::uint64_t>(field_class);
            return json{{"pointer",pointer},{"next",read<std::uintptr_t>(pointer+0x18)},
                {"owner",read<std::uint64_t>(pointer+0x10)},{"field_class",field_class},{"type_name",type_name},{"type",name(type_name)},
                {"name",read<std::uint64_t>(pointer+0x20)},{"field_flags",read<std::uint32_t>(pointer+0x28)},
                {"element_size",read<std::int32_t>(pointer+0x34)},{"array_dim",read<std::int32_t>(pointer+0x30)},
                {"offset",read<std::int32_t>(pointer+0x44)},{"flags",read<std::uint64_t>(pointer+0x38)}};
        };
        auto outer=field_snapshot(field);
        const auto offset=outer.at("offset").get<std::int32_t>();
        if(outer.at("type")!="ArrayProperty" || size!=16 || dim!=1 || outer.at("element_size")!=size || outer.at("array_dim")!=dim
           || offset<0 || offset%8 || outer.at("flags")!=flags)
            throw HandlerError("unsupported_signature","Int32 arrays require a 16-byte input layout");
        if(vector_parameter_direction::classify(flags)!=vector_parameter_direction::Direction::input)
            throw HandlerError("unsupported_signature","Int32 arrays require value or const reference input");
        outer["array_flags"]=read<std::uint8_t>(field+0x70);
        const auto inner_pointer=read<std::uintptr_t>(field+0x78);
        outer["inner"]=inner_pointer;
        auto inner=field_snapshot(inner_pointer);
        if(inner.at("type")!="IntProperty" || inner.at("element_size")!=4 || inner.at("array_dim")!=1 || inner.at("offset")!=0)
            throw HandlerError("unsupported_signature","The int32 array requires four-byte IntProperty elements");
        return json{{"outer",std::move(outer)},{"inner",std::move(inner)},{"scalar_type","IntProperty"},
            {"public",{{"container_type","ArrayProperty"},{"element",{{"type","IntProperty"},
                {"element_size",4},{"array_dim",1},{"offset",0}}}}}};
    };
    const auto before=capture();
    if(before!=capture())throw HandlerError("int32_array_metadata_changed","Reflected int32 array metadata changed during validation");
    return before;
}
nlohmann::json GameAccess::capture_enum_array_parameter(std::uintptr_t field,int size,int dim,std::uint64_t flags) const {
    using json=nlohmann::json;
    using game_memory::read;
    const auto capture=[&]() {
        const auto field_snapshot=[&](std::uintptr_t pointer) {
            const auto field_class=read<std::uintptr_t>(pointer+8);
            const auto type_name=read<std::uint64_t>(field_class);
            return json{{"pointer",pointer},{"next",read<std::uintptr_t>(pointer+0x18)},
                {"owner",read<std::uint64_t>(pointer+0x10)},{"field_class",field_class},{"type_name",type_name},{"type",name(type_name)},
                {"name",read<std::uint64_t>(pointer+0x20)},{"field_flags",read<std::uint32_t>(pointer+0x28)},
                {"element_size",read<std::int32_t>(pointer+0x34)},{"array_dim",read<std::int32_t>(pointer+0x30)},
                {"offset",read<std::int32_t>(pointer+0x44)},{"flags",read<std::uint64_t>(pointer+0x38)}};
        };
        auto outer=field_snapshot(field);
        const auto offset=outer.at("offset").get<std::int32_t>();
        if(outer.at("type")!="ArrayProperty" || size!=16 || dim!=1 || outer.at("element_size")!=size || outer.at("array_dim")!=dim
           || offset<0 || offset%8 || outer.at("flags")!=flags || !(flags&0x80) || (flags&(std::uint64_t{0x100}|std::uint64_t{0x08000000}|std::uint64_t{0x400})))
            throw HandlerError("unsupported_signature","Enum arrays require a 16-byte input by value");
        outer["array_flags"]=read<std::uint8_t>(field+0x70);
        const auto inner_pointer=read<std::uintptr_t>(field+0x78);
        outer["inner"]=inner_pointer;
        auto inner=field_snapshot(inner_pointer);
        if(inner.at("type")!="EnumProperty" || inner.at("element_size")!=1 || inner.at("array_dim")!=1 || inner.at("offset")!=0)
            throw HandlerError("unsupported_signature","The enum array requires one-byte EnumProperty elements");
        const auto layout=enum_layout("EnumProperty",1,inner_pointer);
        if(layout.underlying_type!="ByteProperty")
            throw HandlerError("unsupported_enum_underlying_layout","The enum array requires ByteProperty underlying values");
        const auto underlying_pointer=read<std::uintptr_t>(inner_pointer+0x70);
        inner["underlying"]=underlying_pointer;inner["enum"]=reinterpret_cast<std::uintptr_t>(layout.object);
        auto underlying=field_snapshot(underlying_pointer);
        const auto table=enum_table(layout.object);
        json entries=json::array(),public_entries=json::array();
        for(const auto& entry:table.entries) {
            const auto label=name(entry.name);
            entries.push_back({{"name",entry.name},{"label",label},{"value",entry.value}});
            public_entries.push_back({{"name",label},{"value",entry.value}});
        }
        const auto enum_address=reinterpret_cast<std::uintptr_t>(layout.object);
        const json enumeration={{"identity",object_array_parameter_layout::identity_json(serial_identity(table.identity))},
            {"path",table.path},{"entries",std::move(entries)},{"data",read<std::uintptr_t>(enum_address+0x40)},
            {"count",read<std::int32_t>(enum_address+0x48)},{"capacity",read<std::int32_t>(enum_address+0x4c)}};
        return json{{"outer",std::move(outer)},{"inner",std::move(inner)},{"underlying",std::move(underlying)},
            {"underlying_type",layout.underlying_type},{"enum",enumeration},
            {"public",{{"container_type","ArrayProperty"},{"element",{{"type","EnumProperty"},{"element_size",1},{"array_dim",1},{"offset",0},
                {"enum",{{"path",table.path},{"underlying_type",layout.underlying_type},{"entries",std::move(public_entries)}}}}}}}};
    };
    const auto before=capture();
    if(before!=capture())throw HandlerError("enum_array_metadata_changed","Reflected enum array metadata changed during validation");
    return before;
}

} // namespace g1r_mcp
