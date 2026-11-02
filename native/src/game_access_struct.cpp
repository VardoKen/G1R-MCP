#include "g1r_mcp/game_access.hpp"
#include "g1r_mcp/game_memory.hpp"
#include "g1r_mcp/array_page.hpp"
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
        std::optional<scalar_struct_values::Snapshot> quat;
        std::optional<linear_color_layout::Snapshot> color;
        std::optional<scalar_struct_values::Snapshot> gameplay_tag_array;
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
                } else if(type=="ArrayProperty") {
                    auto& snapshot=child.gameplay_tag_array.emplace();
                    snapshot.field=read<uintptr_t>(field+0x78);
                    snapshot.next=read<uintptr_t>(snapshot.field+0x18);snapshot.field_class=read<uintptr_t>(snapshot.field+8);
                    snapshot.owner=read<uint64_t>(snapshot.field+0x10);snapshot.name=read<uint64_t>(snapshot.field+0x20);
                    snapshot.type_name=read<uint64_t>(snapshot.field_class);snapshot.flags=read<uint64_t>(snapshot.field+0x38);
                    snapshot.offset=read<int32_t>(snapshot.field+0x44);snapshot.element_size=read<int32_t>(snapshot.field+0x34);
                    snapshot.array_dim=read<int32_t>(snapshot.field+0x30);
                    if(name(snapshot.type_name)!="StructProperty")
                        throw HandlerError("unsupported_struct_field_type","The array requires exact GameplayTags.GameplayTag elements");
                    snapshot.structure=read<uintptr_t>(snapshot.field+0x70);
                    snapshot.identity=serial_identity(validate_object(reinterpret_cast<void*>(snapshot.structure)));
                    validate_class(reinterpret_cast<void*>(snapshot.identity.object_class));
                    snapshot.path=object_path(reinterpret_cast<void*>(snapshot.structure));snapshot.struct_name=name(snapshot.identity.name);
                    snapshot.head=read<uintptr_t>(snapshot.structure+0x50);snapshot.super=read<uintptr_t>(snapshot.structure+0x40);
                    snapshot.layout={read<int32_t>(snapshot.structure+0x58),read<int32_t>(snapshot.structure+0x5c),snapshot.super!=0,{}};
                    if(snapshot.path!="/Script/GameplayTags.GameplayTag")
                        throw HandlerError("unsupported_struct_field_type","The array requires exact GameplayTags.GameplayTag elements");
                    if(snapshot.array_dim!=1 || snapshot.element_size<1 || snapshot.layout.properties_size!=snapshot.element_size)
                        throw HandlerError("unsupported_struct_field_layout","GameplayTag elements require a matching reflected size and ArrayDim");
                    std::set<uintptr_t> tag_fields;bool has_tag_name=false;
                    for(auto tag_field=snapshot.head;tag_field;tag_field=read<uintptr_t>(tag_field+0x18)) {
                        if(!tag_fields.insert(tag_field).second)
                            throw HandlerError("struct_scan_incomplete","GameplayTag field chain is cyclic");
                        scalar_struct_values::ComponentMetadata tag;
                        tag.pointer=tag_field;tag.next=read<uintptr_t>(tag_field+0x18);tag.field_class=read<uintptr_t>(tag_field+8);
                        tag.owner=read<uint64_t>(tag_field+0x10);tag.name=read<uint64_t>(tag_field+0x20);
                        tag.type_name=read<uint64_t>(tag.field_class);tag.flags=read<uint64_t>(tag_field+0x38);
                        tag.component={name(tag.name),read<int32_t>(tag_field+0x44),
                            {name(tag.type_name),read<int32_t>(tag_field+0x34),read<int32_t>(tag_field+0x30)}};
                        snapshot.layout.components.push_back(tag.component);snapshot.components.push_back(tag);
                        if(tag.component.name!="TagName")continue;
                        if(has_tag_name || tag.component.layout.type!="NameProperty" || tag.component.layout.element_size!=8
                           || tag.component.layout.array_dim!=1 || tag.component.offset<0 || snapshot.element_size<8
                           || tag.component.offset>snapshot.element_size-8)
                            throw HandlerError("unsupported_struct_field_layout","GameplayTag requires one own TagName NameProperty");
                        has_tag_name=true;
                    }
                    if(!has_tag_name)
                        throw HandlerError("unsupported_struct_field_layout","GameplayTag requires an own TagName NameProperty");
                } else if(type=="StructProperty") {
                    const auto pointer=read<uintptr_t>(field+0x70);child.auxiliary["structure"]=identity(pointer);
                    const auto path=object_path(reinterpret_cast<void*>(pointer));child.auxiliary["struct_path"]=path;
                    if(path=="/Script/CoreUObject.Vector")child.vector=capture_vector_parameter(field,layout.element_size,layout.array_dim);
                    else if(path=="/Script/CoreUObject.Transform")child.transform=capture_transform_parameter(field,layout.element_size,layout.array_dim);
                    else if(path=="/Script/CoreUObject.LinearColor")child.color=capture_linear_color_parameter(field,layout.element_size,layout.array_dim);
                    else if(path=="/Script/CoreUObject.Rotator" || path=="/Script/CoreUObject.Quat") {
                        auto snapshot=scalar_struct_values::capture(field,layout.element_size,layout.array_dim,
                            []<class Value>(uintptr_t address){return read<Value>(address);},
                            [this](uint64_t value){return name(value);},
                            [this](uintptr_t value){return serial_identity(validate_object(reinterpret_cast<void*>(value)));},
                            [this](uintptr_t value){validate_class(reinterpret_cast<void*>(value));},
                            [this](uintptr_t value){return object_path(reinterpret_cast<void*>(value));});
                        if(path=="/Script/CoreUObject.Rotator") {
                            rotator_arguments::validate(snapshot);child.rotator=std::move(snapshot);
                        } else {
                            if(snapshot.path!="/Script/CoreUObject.Quat" || snapshot.struct_name!="Quat")
                                throw HandlerError("unsupported_transform_type","Rotation requires exact CoreUObject.Quat");
                            transform_layout::QuatLayout quaternion{snapshot.layout.properties_size,snapshot.layout.min_alignment,snapshot.layout.has_super,{}};
                            for(const auto& component:snapshot.layout.components)
                                quaternion.components.push_back({component.name,component.layout.type,component.layout.element_size,component.offset,component.layout.array_dim});
                            transform_layout::validate_quat_layout(quaternion);child.quat=std::move(snapshot);
                        }
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
    std::vector<std::pair<uintptr_t,ArrayHeader>> copied_arrays;
    for(const auto& child:before.children) {
        const auto& component=child.field.component;const auto& layout=component.layout;
        json item={{"name",component.name},{"type",layout.type},{"element_size",layout.element_size},
            {"offset",component.offset},{"array_dim",layout.array_dim},{"supported",false}};
        if(child.auxiliary.contains("struct_path"))item["struct_path"]=child.auxiliary.at("struct_path");
        if(child.gameplay_tag_array && !child.gameplay_tag_array->path.empty())
            item["element_struct_path"]=child.gameplay_tag_array->path;
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
            else if(child.quat)value=scalar_struct_values::decode(child.quat->layout,copied);
            else if(child.color)value=linear_color_arguments::decode(child.color->layout,copied);
            else if(child.gameplay_tag_array) {
                if(layout.element_size!=sizeof(ArrayHeader))
                    throw HandlerError("unsupported_struct_field_layout","An array field requires a 16-byte header");
                const auto& snapshot=*child.gameplay_tag_array;
                const auto& tag=*std::find_if(snapshot.components.begin(),snapshot.components.end(),
                    [](const auto& field){return field.component.name=="TagName";});
                const auto header=read<ArrayHeader>(address);
                const auto page=array_page(header,snapshot.element_size,snapshot.array_dim,0,
                    header.count>0?static_cast<size_t>(header.count):size_t{1});
                std::vector<std::byte> payload(page.bytes);
                if(page.bytes && !game_memory::copy(payload.data(),reinterpret_cast<void*>(page.begin),page.bytes))
                    throw HandlerError("invalid_memory","GameplayTag array bytes are unavailable");
                value=json::array();item["total_count"]=header.count;
                for(int32_t index=0;index<page.count;++index) {
                    const auto element=std::span<const std::byte>(payload).subspan(
                        static_cast<size_t>(index)*static_cast<size_t>(snapshot.element_size),static_cast<size_t>(snapshot.element_size));
                    value.push_back({{"TagName",property_value(reinterpret_cast<uintptr_t>(element.data())+static_cast<uintptr_t>(tag.component.offset),
                        tag.component.layout.type,tag.component.layout.element_size,tag.pointer)}});
                }
                const auto live_header_address=reinterpret_cast<uintptr_t>(object)+static_cast<uintptr_t>(before.structure.offset)
                    +static_cast<uintptr_t>(component.offset);
                if(!same_array_header(header,read<ArrayHeader>(live_header_address)))
                    throw HandlerError("array_changed","The GameplayTag array header changed while being copied");
                copied_arrays.emplace_back(live_header_address,header);
            }
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
    for(const auto& [header_address,header]:copied_arrays)
        if(!same_array_header(header,read<ArrayHeader>(header_address)))
            throw HandlerError("array_changed","The GameplayTag array header changed while the struct was copied");
    return {{"source","live_native"},{"session_id",handle_session_},{"handle",handle},{"property_name",property_name},
            {"struct_path",before.structure.path},{"properties_size",before.structure.layout.properties_size},
            {"min_alignment",before.structure.layout.min_alignment},{"components",components},
            {"consistency","copied_bytes_then_metadata_revalidated"},{"partial",partial},{"truncated",false}};
}
} // namespace g1r_mcp
