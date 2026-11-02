#include "g1r_mcp/game_access.hpp"
#include "g1r_mcp/game_memory.hpp"
#include "g1r_mcp/scalar_struct_values.hpp"
#include "g1r_mcp/vector_arguments.hpp"
#include "g1r_mcp/transform_arguments.hpp"
#include "g1r_mcp/rotator_arguments.hpp"
#include "g1r_mcp/linear_color_arguments.hpp"
#include <optional>
#include <set>
#include <span>
#include <vector>
namespace g1r_mcp {
using json=nlohmann::json;
using game_memory::read;
json GameAccess::read_struct(void* object,const std::string& handle,const std::string& property_name) {
    if(property_name.empty() || property_name.find('\0')!=std::string::npos)
        throw HandlerError("invalid_struct_params","property_name must be nonempty and contain no NUL");
    const auto initial=validate_object(object);
    struct Child {
        scalar_struct_values::ComponentMetadata field;
        json auxiliary=json::object();
        std::optional<vector_layout::Snapshot> vector;
        std::optional<transform_layout::Snapshot> transform;
        std::optional<rotator_arguments::Snapshot> rotator;
        std::optional<linear_color_layout::Snapshot> color;
        json error=nullptr;
        bool operator==(const Child&) const=default;
    };
    struct Metadata {
        uintptr_t owner{};
        int owner_size{};
        Handle owner_identity;
        scalar_struct_values::Snapshot structure;
        std::vector<Child> children;
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
        if(!selected)throw HandlerError("property_not_found","Struct property not found");
        result.owner_identity=validate_object(reinterpret_cast<void*>(result.owner));
        if(game_memory::field_type(selected,[this](uint64_t value){return name(value);})!="StructProperty")
            throw HandlerError("unsupported_struct_type","The selected property is not an exact StructProperty");
        const auto size=read<int32_t>(selected+0x34),dim=read<int32_t>(selected+0x30);
        result.owner_size=read<int32_t>(result.owner+0x58);
        if(dim!=1 || size<1)
            throw HandlerError("unsupported_struct_layout","The outer struct property is unsupported");
        auto& structure=result.structure;
        structure.field=selected;structure.next=read<uintptr_t>(selected+0x18);
        structure.field_class=read<uintptr_t>(selected+8);structure.owner=read<uint64_t>(selected+0x10);
        structure.name=read<uint64_t>(selected+0x20);structure.type_name=read<uint64_t>(structure.field_class);
        structure.flags=read<uint64_t>(selected+0x38);structure.offset=read<int32_t>(selected+0x44);
        structure.element_size=size;structure.array_dim=dim;structure.structure=read<uintptr_t>(selected+0x70);
        structure.identity=serial_identity(validate_object(reinterpret_cast<void*>(structure.structure)));
        validate_class(reinterpret_cast<void*>(structure.identity.object_class));
        structure.path=object_path(reinterpret_cast<void*>(structure.structure));structure.struct_name=name(structure.identity.name);
        structure.head=read<uintptr_t>(structure.structure+0x50);structure.super=read<uintptr_t>(structure.structure+0x40);
        structure.layout={read<int32_t>(structure.structure+0x58),read<int32_t>(structure.structure+0x5c),structure.super!=0,{}};
        if(structure.layout.properties_size!=size || structure.super || structure.layout.min_alignment<1
           || (structure.layout.min_alignment&(structure.layout.min_alignment-1)) || size%structure.layout.min_alignment)
            throw HandlerError("unsupported_struct_layout","The struct requires matching reflected size, alignment, and own fields");
        auto identity=[&](uintptr_t pointer) {
            const auto value=serial_identity(validate_object(reinterpret_cast<void*>(pointer)));
            return json{{"pointer",pointer},{"class",value.object_class},{"index",value.index},{"name",value.name},
                {"serial",value.serial},{"class_index",value.class_index},{"class_name",value.class_name},{"class_serial",value.class_serial}};
        };
        std::set<uintptr_t> children;
        for(auto field=structure.head;field;field=read<uintptr_t>(field+0x18)) {
            if(!children.insert(field).second)throw HandlerError("struct_scan_incomplete","Struct field chain is cyclic");
            Child child;auto& metadata=child.field;
            metadata.pointer=field;metadata.next=read<uintptr_t>(field+0x18);metadata.field_class=read<uintptr_t>(field+8);
            metadata.owner=read<uint64_t>(field+0x10);metadata.name=read<uint64_t>(field+0x20);
            metadata.type_name=read<uint64_t>(metadata.field_class);metadata.flags=read<uint64_t>(field+0x38);
            metadata.component={name(metadata.name),read<int32_t>(field+0x44),
                {name(metadata.type_name),read<int32_t>(field+0x34),read<int32_t>(field+0x30)}};
            auto& layout=metadata.component.layout;const auto& type=layout.type;
            try {
                if(type=="BoolProperty") {
                    layout.field_size=read<uint8_t>(field+0x70);layout.byte_offset=read<uint8_t>(field+0x71);
                    layout.byte_mask=read<uint8_t>(field+0x72);layout.field_mask=read<uint8_t>(field+0x73);
                } else if(type=="ObjectProperty" || type=="ClassProperty") {
                    child.auxiliary["declared"]=identity(read<uintptr_t>(field+0x70));
                    if(type=="ClassProperty")child.auxiliary["meta"]=identity(read<uintptr_t>(field+0x78));
                } else if(type=="EnumProperty" || type=="ByteProperty") {
                    const auto enumeration=enum_layout(type,layout.element_size,field);
                    child.auxiliary["underlying_type"]=enumeration.underlying_type;
                    if(type=="EnumProperty") {
                        const auto underlying=read<uintptr_t>(field+0x70),klass=read<uintptr_t>(underlying+8);
                        child.auxiliary["underlying"]={underlying,klass,read<uint64_t>(klass),read<uint64_t>(underlying+0x10),
                            read<uintptr_t>(underlying+0x18),read<uint64_t>(underlying+0x20),read<int32_t>(underlying+0x30),
                            read<int32_t>(underlying+0x34),read<uint64_t>(underlying+0x38),read<int32_t>(underlying+0x44)};
                    }
                    if(enumeration.object)child.auxiliary["enum"]=identity(reinterpret_cast<uintptr_t>(enumeration.object));
                } else if(type=="StructProperty") {
                    const auto pointer=read<uintptr_t>(field+0x70);child.auxiliary["structure"]=identity(pointer);
                    const auto path=object_path(reinterpret_cast<void*>(pointer));child.auxiliary["struct_path"]=path;
                    if(path=="/Script/CoreUObject.Vector")child.vector=capture_vector_parameter(field,layout.element_size,layout.array_dim);
                    else if(path=="/Script/CoreUObject.Transform")child.transform=capture_transform_parameter(field,layout.element_size,layout.array_dim);
                    else if(path=="/Script/CoreUObject.LinearColor")child.color=capture_linear_color_parameter(field,layout.element_size,layout.array_dim);
                    else if(path=="/Script/CoreUObject.Rotator") {
                        child.rotator=scalar_struct_values::capture(field,layout.element_size,layout.array_dim,
                            []<class Value>(uintptr_t address){return read<Value>(address);},
                            [this](uint64_t value){return name(value);},
                            [this](uintptr_t value){return serial_identity(validate_object(reinterpret_cast<void*>(value)));},
                            [this](uintptr_t value){validate_class(reinterpret_cast<void*>(value));},
                            [this](uintptr_t value){return object_path(reinterpret_cast<void*>(value));});
                        rotator_arguments::validate(*child.rotator);
                    }
                }
            } catch(const HandlerError& error) {child.error={{"code",error.code()},{"message",error.what()}};}
            structure.layout.components.push_back(metadata.component);structure.components.push_back(metadata);
            result.children.push_back(std::move(child));
        }
        if(result.structure.offset<0 || result.owner_size<size || result.structure.offset>result.owner_size-size)
            throw HandlerError("unsupported_struct_layout","The outer struct property is unsupported");
        return result;
    };
    const auto before=capture();
    std::vector<std::byte> bytes(static_cast<size_t>(before.structure.element_size));
    const auto address=reinterpret_cast<uintptr_t>(object)+static_cast<uintptr_t>(before.structure.offset);
    if(address<reinterpret_cast<uintptr_t>(object)
       || !game_memory::copy(bytes.data(),reinterpret_cast<void*>(address),bytes.size()))
        throw HandlerError("invalid_memory","Struct bytes are unavailable");
    json components=json::array();bool partial=false;
    for(const auto& child:before.children) {
        const auto& component=child.field.component;const auto& layout=component.layout;
        json item={{"name",component.name},{"type",layout.type},{"element_size",layout.element_size},
            {"offset",component.offset},{"array_dim",layout.array_dim},{"supported",false}};
        if(child.auxiliary.contains("struct_path"))item["struct_path"]=child.auxiliary.at("struct_path");
        try {
            if(!child.error.is_null())throw HandlerError(child.error.at("code").get<std::string>(),child.error.at("message").get<std::string>());
            if(layout.array_dim!=1 || layout.element_size<1 || component.offset<0
               || layout.element_size>before.structure.element_size || component.offset>before.structure.element_size-layout.element_size)
                throw HandlerError("unsupported_struct_field_layout","The struct field layout is unsupported");
            const auto copied=std::span<const std::byte>(bytes).subspan(static_cast<size_t>(component.offset),static_cast<size_t>(layout.element_size));
            const auto address=reinterpret_cast<uintptr_t>(copied.data());json value;
            if(child.vector)value=vector_arguments::decode(child.vector->layout,copied);
            else if(child.transform)value=transform_arguments::decode(child.transform->layout,copied);
            else if(child.rotator)value=rotator_arguments::decode(child.rotator->layout,copied);
            else if(child.color)value=linear_color_arguments::decode(child.color->layout,copied);
            else if(layout.type=="EnumProperty" || layout.type=="ByteProperty") {
                const auto result=enum_property(address,layout.type,layout.element_size,child.field.pointer);
                value=result.at("value");if(result.contains("enum"))item["enum"]=result.at("enum");
            } else value=property_value(address,layout.type,layout.element_size,child.field.pointer);
            if(value.is_null() && layout.type!="ObjectProperty" && layout.type!="ClassProperty")
                throw HandlerError("unsupported_struct_field_type","The struct field type is unsupported");
            item["value"]=std::move(value);item["supported"]=true;
        } catch(const HandlerError& error) {item["error"]={{"code",error.code()},{"message",error.what()}};partial=true;}
        components.push_back(std::move(item));
    }
    const auto after=capture();
    if(before.owner!=after.owner || before.owner_size!=after.owner_size || !(before.structure==after.structure) || before.children!=after.children
       || !same_serial_identity(serial_identity(before.owner_identity),serial_identity(after.owner_identity))
       || !same_serial_identity(serial_identity(initial),serial_identity(validate_object(object))))
        throw HandlerError("struct_metadata_changed","Struct metadata or identity changed while being copied");
    if(resolve(handle)!=object)throw HandlerError("stale_handle","The struct handle changed");
    return {{"source","live_native"},{"session_id",handle_session_},{"handle",handle},{"property_name",property_name},
            {"struct_path",before.structure.path},{"properties_size",before.structure.layout.properties_size},
            {"min_alignment",before.structure.layout.min_alignment},{"components",components},
            {"consistency","copied_bytes_then_metadata_revalidated"},{"partial",partial},{"truncated",false}};
}
} // namespace g1r_mcp
