#include "g1r_mcp/game_access.hpp"
#include "g1r_mcp/transport.hpp"
#include "g1r_mcp/scalar_arguments.hpp"
#include "g1r_mcp/object_arguments.hpp"
#include "g1r_mcp/class_arguments.hpp"
#include "g1r_mcp/object_query.hpp"
#include "g1r_mcp/function_query.hpp"
#include "g1r_mcp/function_contract.hpp"
#include "g1r_mcp/selected_property_query.hpp"
#include "g1r_mcp/name_return.hpp"
#include "g1r_mcp/name_argument.hpp"
#include "g1r_mcp/string_return.hpp"
#include "g1r_mcp/enum_values.hpp"
#include "g1r_mcp/enum_arguments.hpp"
#include "g1r_mcp/vector_layout.hpp"
#include "g1r_mcp/vector_arguments.hpp"
#include "g1r_mcp/vector_parameter_direction.hpp"
#include "g1r_mcp/parameter_direction.hpp"
#include "g1r_mcp/scalar_outputs.hpp"
#include "g1r_mcp/scalar_struct_values.hpp"
#include "g1r_mcp/rotator_arguments.hpp"
#include "g1r_mcp/aligned_parameter_buffer.hpp"
#include "g1r_mcp/transform_layout.hpp"
#include "g1r_mcp/transform_arguments.hpp"
#include "g1r_mcp/string_arguments.hpp"
#include "g1r_mcp/string_input_lifetime.hpp"
#include "g1r_mcp/object_array_input_lifetime.hpp"
#include "g1r_mcp/linear_color_layout.hpp"
#include "g1r_mcp/linear_color_arguments.hpp"
#include "g1r_mcp/cleanup_once.hpp"
#include "g1r_mcp/native_exception.hpp"
#include "g1r_mcp/as_exception_capture.hpp"
#include <MinHook.h>
#include <bcrypt.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <optional>
#include <sstream>
#include <set>
#include <vector>

namespace g1r_mcp {
using json = nlohmann::json;
namespace {
uint64_t steady_ms() {
    return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count());
}
constexpr uint64_t kRequestWaitMs=2500;
constexpr uint64_t kReferenceParm=vector_parameter_direction::reference_parm;
constexpr uint64_t kConstParm=vector_parameter_direction::const_parm;
bool supported_scalar_output_type(const std::string& type) {
    return type=="BoolProperty" || type=="IntProperty" || type=="Int64Property"
        || type=="UInt32Property" || type=="UInt64Property" || type=="FloatProperty" || type=="DoubleProperty";
}
scalar_outputs::Layout capture_scalar_output_layout(uintptr_t field,const std::string& type,int size,int dim);
json scalar_output_metadata(const scalar_outputs::Layout& layout) {
    json result={{"type",layout.type},{"element_size",layout.element_size},{"array_dim",layout.array_dim}};
    if(layout.type=="BoolProperty")result["bool_layout"]={{"field_size",layout.field_size},{"byte_offset",layout.byte_offset},
        {"byte_mask",layout.byte_mask},{"field_mask",layout.field_mask}};
    return result;
}
json scalar_struct_snapshot_json(const scalar_struct_values::Snapshot& value,bool raw=true) {
    json components=json::array();
    for(const auto& child:value.components) {
        auto item=scalar_output_metadata(child.component.layout);
        item["name"]=child.component.name;item["offset"]=child.component.offset;
        if(raw)item.update({{"pointer",child.pointer},{"next",child.next},{"field_class",child.field_class},
            {"owner",child.owner},{"field_name",child.name},{"type_name",child.type_name},{"flags",child.flags}});
        components.push_back(std::move(item));
    }
    json result={{"struct_path",value.path},{"struct_name",value.struct_name},{"properties_size",value.layout.properties_size},
        {"min_alignment",value.layout.min_alignment},{"components",std::move(components)}};
    if(raw) {
        const auto& identity=value.identity;
        result.update({{"field",value.field},{"next",value.next},{"field_class",value.field_class},{"field_owner",value.owner},
            {"field_name",value.name},{"field_type_name",value.type_name},{"flags",value.flags},{"offset",value.offset},
            {"structure",value.structure},{"head",value.head},{"super",value.super},{"element_size",value.element_size},
            {"array_dim",value.array_dim},{"identity",{{"object",identity.object},{"object_class",identity.object_class},
                {"index",identity.index},{"name",identity.name},{"serial",identity.serial},{"class_index",identity.class_index},
                {"class_name",identity.class_name},{"class_serial",identity.class_serial}}}});
    }
    return result;
}
json vector_snapshot_json(const vector_layout::Snapshot& value,bool raw=true) {
    json components=json::array();
    for(const auto& child:value.components) {
        json item={{"name",child.component.name},{"type",child.component.type},{"element_size",child.component.element_size},
            {"offset",child.component.offset},{"array_dim",child.component.array_dim}};
        if(raw)item.update({{"pointer",child.pointer},{"next",child.next},{"field_class",child.field_class},
            {"owner",child.owner},{"field_name",child.name},{"type_name",child.type_name}});
        components.push_back(std::move(item));
    }
    json result={{"struct_path",value.path},{"struct_name",value.struct_name},{"properties_size",value.layout.properties_size},
        {"min_alignment",value.layout.min_alignment},{"components",components}};
    if(raw) {
        const auto& identity=value.identity;
        result.update({{"field",value.field},{"field_class",value.field_class},{"field_owner",value.field_owner},
            {"field_name",value.field_name},{"field_type_name",value.field_type_name},{"structure",value.structure},
            {"head",value.head},{"super",value.super},{"element_size",value.element_size},{"array_dim",value.array_dim},
            {"identity",{{"object",identity.object},{"object_class",identity.object_class},{"index",identity.index},
                {"name",identity.name},{"serial",identity.serial},{"class_index",identity.class_index},
                {"class_name",identity.class_name},{"class_serial",identity.class_serial}}}});
    }
    return result;
}
json transform_snapshot_json(const transform_layout::Snapshot& value,bool raw=true) {
    json members=json::array();
    for(const auto& member:value.members) {
        json item={{"name",member.layout.name},{"type",member.layout.type},{"element_size",member.layout.element_size},
            {"offset",member.layout.offset},{"array_dim",member.layout.array_dim}};
        if(member.layout.name=="Rotation")item["struct"]=vector_snapshot_json(value.rotation.metadata,raw);
        else if(member.layout.name=="Translation")item["struct"]=vector_snapshot_json(value.translation,raw);
        else if(member.layout.name=="Scale3D")item["struct"]=vector_snapshot_json(value.scale3d,raw);
        if(raw)item.update({{"pointer",member.pointer},{"next",member.next},{"field_class",member.field_class},
            {"structure",member.structure},{"owner",member.owner},{"field_name",member.name},{"type_name",member.type_name},{"flags",member.flags}});
        members.push_back(std::move(item));
    }
    json result={{"struct_path",value.path},{"struct_name",value.struct_name},{"properties_size",value.layout.properties_size},
        {"min_alignment",value.layout.min_alignment},{"members",members}};
    if(raw) {
        const auto& identity=value.identity;
        result.update({{"field",value.field},{"field_class",value.field_class},{"field_owner",value.field_owner},
            {"field_name",value.field_name},{"field_type_name",value.field_type_name},{"field_flags",value.field_flags},
            {"structure",value.structure},{"head",value.head},{"super",value.super},{"element_size",value.element_size},{"array_dim",value.array_dim},
            {"identity",{{"object",identity.object},{"object_class",identity.object_class},{"index",identity.index},{"name",identity.name},
                {"serial",identity.serial},{"class_index",identity.class_index},{"class_name",identity.class_name},{"class_serial",identity.class_serial}}}});
    }
    return result;
}
json linear_color_snapshot_json(const linear_color_layout::Snapshot& value,bool raw=true) {
    json components=json::array();
    for(const auto& child:value.components) {
        json item={{"name",child.component.name},{"type",child.component.type},{"element_size",child.component.element_size},
            {"offset",child.component.offset},{"array_dim",child.component.array_dim}};
        if(raw)item.update({{"pointer",child.pointer},{"next",child.next},{"field_class",child.field_class},{"owner",child.owner},
            {"field_name",child.name},{"type_name",child.type_name},{"flags",child.flags},{"field_flags",child.field_flags}});
        components.push_back(std::move(item));
    }
    json result={{"struct_path",value.path},{"struct_name",value.struct_name},{"properties_size",value.layout.properties_size},
        {"min_alignment",value.layout.min_alignment},{"components",components}};
    if(raw) {
        const auto& identity=value.identity;
        result.update({{"field",value.field},{"next",value.next},{"field_class",value.field_class},{"field_owner",value.field_owner},
            {"field_name",value.field_name},{"field_type_name",value.field_type_name},{"flags",value.flags},{"field_flags",value.field_flags},
            {"offset",value.offset},{"structure",value.structure},{"head",value.head},{"super",value.super},{"element_size",value.element_size},
            {"array_dim",value.array_dim},{"identity",{{"object",identity.object},{"object_class",identity.object_class},{"index",identity.index},
                {"name",identity.name},{"serial",identity.serial},{"class_index",identity.class_index},{"class_name",identity.class_name},{"class_serial",identity.class_serial}}}});
    }
    return result;
}
void diagnostic(const char* text) {
    HMODULE self{};GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
        reinterpret_cast<LPCWSTR>(&diagnostic),&self);
    wchar_t path[32768]{};GetModuleFileNameW(self,path,32768);std::wstring file(path);file=file.substr(0,file.find_last_of(L"\\/"))+L"\\native-tick.log";
    std::ofstream output(file,std::ios::app);output<<text<<"\n";
}
constexpr uintptr_t kObjects = 0x9e9fbd0;
constexpr uintptr_t kNames = 0x9de8d80;
constexpr uintptr_t kTick = 0x3fbced0;
constexpr uintptr_t kProcessEvent = 0x13a6ad0;
constexpr uintptr_t kFree = 0x1081690;
constexpr uintptr_t kAngelscriptManager = 0xa02fdb8;
constexpr uintptr_t kFormatDiagnostics = 0x417d2b0;
constexpr uintptr_t kASExceptionLogger = 0x4183050;
constexpr uint8_t kASExceptionLoggerBytes[] = {0x48,0x89,0x5c,0x24,0x18,0x56,0x48,0x81,0xec,0xc0,0x01,0x00,0x00};
constexpr uintptr_t kASStackTrace = 0x4180060;
constexpr uint8_t kASStackTraceBytes[] = {0x40,0x55,0x53,0x57,0x41,0x55,0x41,0x56,0x48,0x8d,0xac,0x24,0xa0,0xfc,0xff,0xff};
constexpr uintptr_t kStringConstruct = 0x10316e0;
constexpr uintptr_t kNameConstruct = 0x11b37a0;
constexpr uintptr_t kObjectArrayRealloc = 0x108f6a0;
constexpr uint8_t kObjectArrayReallocBytes[]={0x48,0x89,0x5c,0x24,0x08,0x48,0x89,0x74,0x24,0x10,0x57,0x48,0x83,0xec,0x20,0x48,
    0x8b,0xf1,0x41,0x8b,0xd8,0x48,0x8b,0x0d,0x2c,0x16,0xd2,0x08,0x48,0x8b,0xfa,0x48};
constexpr uint8_t kStringConstructBytes[]={0x48,0x89,0x6c,0x24,0x18,0x56,0x41,0x56,0x41,0x57,0x48,0x83,0xec,0x20,0x45,0x33,
    0xff,0x49,0x63,0xe8,0x4c,0x89,0x39,0x4c,0x8b,0xf2,0x4c,0x89,0x79,0x08,0x48,0x8b};
constexpr uint8_t kStringFreeBytes[]={0x48,0x85,0xc9,0x74,0x2e,0x53,0x48,0x83,0xec,0x20,0x48,0x8b,0xd9,0x48,0x8b,0x0d,
    0x44,0xf6,0xd2,0x08,0x48,0x85,0xc9,0x75,0x0c,0xe8,0x92,0x00,0x00,0x00,0x48,0x8b};
constexpr const char* kHash = "0e369f64ba50aeb85925119ded60e0449dbc4917e38b46cf7251e533d422820b";
constexpr uint8_t kTickBytes[] = {0x4c,0x8b,0xdc,0x55,0x57,0x41,0x54,0x49,0x8d,0xab,0x08,0xfc,0xff,0xff,0x48,0x81,0xec,0xe0,0x04,0x00,0x00};
constexpr uint8_t kEventBytes[] = {0x40,0x55,0x56,0x57,0x41,0x54,0x41,0x55,0x41,0x56,0x41,0x57,0x48,0x81,0xec,0x70,0x01,0x00,0x00,0x48,0x8d,0x6c,0x24,0x30};

bool copy_memory(void* destination, const void* source, size_t count) {
    if (!source) return false;
    __try { std::memcpy(destination, source, count); return true; }
    __except(EXCEPTION_EXECUTE_HANDLER) { return false; }
}
template<class T> T read(uintptr_t address) {
    T value{};
    if (!copy_memory(&value, reinterpret_cast<void*>(address), sizeof(T)))
        throw HandlerError("invalid_memory", "Память объекта недоступна");
    return value;
}
bool call_event(void* function, void* object, void* event, void* params,native_exception::Diagnostic* fault) {
    __try { reinterpret_cast<void(__fastcall*)(void*,void*,void*)>(function)(object,event,params); return true; }
    __except(native_exception::capture(GetExceptionInformation(),fault)) { return false; }
}
bool free_string(uintptr_t free_address, void* memory) {
    if (!memory) return true;
    __try { reinterpret_cast<void(__fastcall*)(void*)>(free_address)(memory); return true; }
    __except(EXCEPTION_EXECUTE_HANDLER) { return false; }
}
bool construct_string(uintptr_t constructor,g1r_mcp::StringHeader* header,const char16_t* text,int units) {
    __try {reinterpret_cast<void(__fastcall*)(g1r_mcp::StringHeader*,const char16_t*,int)>(constructor)(header,text,units);return true;}
    __except(EXCEPTION_EXECUTE_HANDLER) {return false;}
}
bool construct_name(uintptr_t constructor,uint64_t* value,const char16_t* text) {
    __try {reinterpret_cast<uint64_t*(__fastcall*)(uint64_t*,const char16_t*,uint32_t)>(constructor)(value,text,1);return true;}
    __except(EXCEPTION_EXECUTE_HANDLER) {return false;}
}
bool allocate_object_array(uintptr_t allocator,size_t bytes,void** result) {
    __try {*result=reinterpret_cast<void*(__fastcall*)(void*,size_t,uint32_t)>(allocator)(nullptr,bytes,8);return true;}
    __except(EXCEPTION_EXECUTE_HANDLER) {return false;}
}
std::string utf8(const wchar_t* value, int length) {
    if (length <= 0) return {};
    int count = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value,length,nullptr,0,nullptr,nullptr);
    if (!count) throw HandlerError("invalid_string", "Некорректная строка UTF-16");
    std::string result(count,'\0');
    WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,value,length,result.data(),count,nullptr,nullptr);
    return result;
}
std::string copied_string(const g1r_mcp::StringHeader& header) {
    g1r_mcp::validate_string_header(header);
    std::vector<wchar_t> text(static_cast<size_t>(header.count));
    if(header.count && !copy_memory(text.data(),reinterpret_cast<void*>(header.data),
                                   static_cast<size_t>(header.count)*sizeof(wchar_t)))
        throw HandlerError("invalid_memory","Строка недоступна");
    if(header.count && text.back()==0)text.pop_back();
    return utf8(text.data(),static_cast<int>(text.size()));
}
std::string file_hash() {
    wchar_t path[32768]{};
    if (!GetModuleFileNameW(nullptr,path,32768)) return {};
    std::ifstream file(path,std::ios::binary);
    if (!file) return {};
    BCRYPT_ALG_HANDLE algorithm{}; BCRYPT_HASH_HANDLE hash{};
    if (BCryptOpenAlgorithmProvider(&algorithm,BCRYPT_SHA256_ALGORITHM,nullptr,0)<0) return {};
    DWORD object_size{}, digest_size{}, received{};
    BCryptGetProperty(algorithm,BCRYPT_OBJECT_LENGTH,reinterpret_cast<PUCHAR>(&object_size),sizeof(object_size),&received,0);
    BCryptGetProperty(algorithm,BCRYPT_HASH_LENGTH,reinterpret_cast<PUCHAR>(&digest_size),sizeof(digest_size),&received,0);
    std::vector<unsigned char> object(object_size),digest(digest_size);
    if (BCryptCreateHash(algorithm,&hash,object.data(),object_size,nullptr,0,0)<0) {BCryptCloseAlgorithmProvider(algorithm,0);return {};}
    std::array<char,65536> buffer{};
    while(file) {file.read(buffer.data(),buffer.size()); auto count=file.gcount(); if(count) BCryptHashData(hash,reinterpret_cast<PUCHAR>(buffer.data()),static_cast<ULONG>(count),0);}
    BCryptFinishHash(hash,digest.data(),digest_size,0); BCryptDestroyHash(hash); BCryptCloseAlgorithmProvider(algorithm,0);
    std::ostringstream output; output<<std::hex<<std::setfill('0'); for(auto byte:digest) output<<std::setw(2)<<static_cast<unsigned>(byte);
    return output.str();
}
int limit_of(const json& params,int default_value=50) {
    int value=params.value("limit",default_value);
    if(value<1 || value>200) throw HandlerError("invalid_limit","Предел должен быть от 1 до 200");
    return value;
}
bool contains(const std::string& value,const std::string& query) {
    std::string a=value,b=query;
    std::transform(a.begin(),a.end(),a.begin(),[](unsigned char c){return static_cast<char>(std::tolower(c));});
    std::transform(b.begin(),b.end(),b.begin(),[](unsigned char c){return static_cast<char>(std::tolower(c));});
    return a.find(b)!=std::string::npos;
}
std::string field_type(uintptr_t field, const std::function<std::string(uint64_t)>& get_name) {
    auto type=read<uintptr_t>(field+8); return get_name(read<uint64_t>(type));
}
json scalar(uintptr_t address,const std::string& type,int size,uintptr_t field) {
    if(type=="BoolProperty") {auto offset=read<uint8_t>(field+0x71),mask=read<uint8_t>(field+0x73);validate_bool_layout(size,offset,mask);return (read<uint8_t>(address+offset)&mask)!=0;}
    if(type=="IntProperty" && size==4) return read<int32_t>(address);
    if(type=="Int64Property" && size==8) return read<int64_t>(address);
    if(type=="UInt32Property" && size==4) return read<uint32_t>(address);
    if(type=="UInt64Property" && size==8) return read<uint64_t>(address);
    if(type=="ByteProperty" && size==1) return read<uint8_t>(address);
    if(type=="FloatProperty" && size==4) {auto v=read<float>(address); return std::isfinite(v)?json(v):json(nullptr);}
    if(type=="DoubleProperty" && size==8) {auto v=read<double>(address);return std::isfinite(v)?json(v):json(nullptr);}
    if(type=="StrProperty" && size==16) {
        return copied_string(read<g1r_mcp::StringHeader>(address));
    }
    return nullptr;
}
scalar_outputs::Layout capture_scalar_output_layout(uintptr_t field,const std::string& type,int size,int dim) {
    scalar_outputs::Layout layout{type,size,dim};
    if(type=="BoolProperty") {
        layout.field_size=read<uint8_t>(field+0x70);layout.byte_offset=read<uint8_t>(field+0x71);
        layout.byte_mask=read<uint8_t>(field+0x72);layout.field_mask=read<uint8_t>(field+0x73);
    }
    scalar_outputs::validate_layout(layout);return layout;
}
}

GameAccess* GameAccess::active_{};
uintptr_t GameAccess::ensure_image_base() {
    if(base_)return base_;
    if(file_hash()!=kHash) {
        startup_error_="exe_hash_mismatch";
        throw HandlerError("exe_hash_mismatch","Требуется подтверждённая сборка игры");
    }
    base_=reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));
    return base_;
}
bool GameAccess::start() {
    if(enabled_) return true;
    try {ensure_image_base();}
    catch(const HandlerError&) {return false;}
    uint8_t tick[sizeof(kTickBytes)]{},event[sizeof(kEventBytes)]{},exception_logger[sizeof(kASExceptionLoggerBytes)]{},stack_trace[sizeof(kASStackTraceBytes)]{};
    if(!copy_memory(tick,reinterpret_cast<void*>(base_+kTick),sizeof(tick)) ||
       !copy_memory(event,reinterpret_cast<void*>(base_+kProcessEvent),sizeof(event)) ||
       !copy_memory(exception_logger,reinterpret_cast<void*>(base_+kASExceptionLogger),sizeof(exception_logger)) ||
       !copy_memory(stack_trace,reinterpret_cast<void*>(base_+kASStackTrace),sizeof(stack_trace)) ||
       std::memcmp(tick,kTickBytes,sizeof(tick)) || std::memcmp(event,kEventBytes,sizeof(event)) ||
       std::memcmp(exception_logger,kASExceptionLoggerBytes,sizeof(exception_logger)) ||
       std::memcmp(stack_trace,kASStackTraceBytes,sizeof(stack_trace))) {
        startup_error_="signature_mismatch";return false;
    }
    auto initialized=MH_Initialize();
    if(initialized!=MH_OK && initialized!=MH_ERROR_ALREADY_INITIALIZED) {startup_error_="hook_initialize_failed";return false;}
    active_=this;process_event_=reinterpret_cast<ProcessEventFn>(base_+kProcessEvent);
    if(MH_CreateHook(reinterpret_cast<void*>(base_+kASExceptionLogger),reinterpret_cast<void*>(&hooked_as_exception_logger),
                     reinterpret_cast<void**>(&original_as_exception_logger_))!=MH_OK) {startup_error_="as_exception_hook_failed";return false;}
    if(MH_EnableHook(reinterpret_cast<void*>(base_+kASExceptionLogger))!=MH_OK) {
        MH_RemoveHook(reinterpret_cast<void*>(base_+kASExceptionLogger));startup_error_="as_exception_hook_failed";return false;
    }
    diagnostic("hook_create_enter");
    if(MH_CreateHook(reinterpret_cast<void*>(base_+kASStackTrace),reinterpret_cast<void*>(&hooked_as_stack_trace),
                     reinterpret_cast<void**>(&original_as_stack_trace_))!=MH_OK) {
        MH_DisableHook(reinterpret_cast<void*>(base_+kASExceptionLogger));MH_RemoveHook(reinterpret_cast<void*>(base_+kASExceptionLogger));
        startup_error_="as_stack_hook_failed";return false;
    }
    if(MH_EnableHook(reinterpret_cast<void*>(base_+kASStackTrace))!=MH_OK) {
        MH_RemoveHook(reinterpret_cast<void*>(base_+kASStackTrace));
        MH_DisableHook(reinterpret_cast<void*>(base_+kASExceptionLogger));MH_RemoveHook(reinterpret_cast<void*>(base_+kASExceptionLogger));
        startup_error_="as_stack_hook_failed";return false;
    }
    if(MH_CreateHook(reinterpret_cast<void*>(base_+kTick),reinterpret_cast<void*>(&hooked_tick),reinterpret_cast<void**>(&original_tick_))!=MH_OK) {
        MH_DisableHook(reinterpret_cast<void*>(base_+kASStackTrace));MH_RemoveHook(reinterpret_cast<void*>(base_+kASStackTrace));
        MH_DisableHook(reinterpret_cast<void*>(base_+kASExceptionLogger));MH_RemoveHook(reinterpret_cast<void*>(base_+kASExceptionLogger));
        startup_error_="tick_hook_failed";return false;
    }
    if(MH_EnableHook(reinterpret_cast<void*>(base_+kTick))!=MH_OK) {
        MH_DisableHook(reinterpret_cast<void*>(base_+kASStackTrace));MH_RemoveHook(reinterpret_cast<void*>(base_+kASStackTrace));
        MH_RemoveHook(reinterpret_cast<void*>(base_+kTick));MH_DisableHook(reinterpret_cast<void*>(base_+kASExceptionLogger));
        MH_RemoveHook(reinterpret_cast<void*>(base_+kASExceptionLogger));startup_error_="tick_hook_failed";return false;
    }
    diagnostic("hook_enabled");enabled_=true;return true;
}
void __fastcall GameAccess::hooked_as_exception_logger(const char* message) {
    as_exception_capture::LoggerScope logger;
    as_exception_capture::record(message,[](uintptr_t address,char& value) {
        return copy_memory(&value,reinterpret_cast<const void*>(address),sizeof(value));
    });
    active_->original_as_exception_logger_(message);
}
void __fastcall GameAccess::hooked_as_stack_trace(void* output) {
    active_->original_as_stack_trace_(output);
    if(!as_exception_capture::active_logger || !as_exception_capture::active ||
       as_exception_capture::active_logger->owner()!=as_exception_capture::active)return;
    std::vector<std::string> frames;
    bool complete=false;
    try {
        ArrayHeader header{};
        if(!copy_memory(&header,output,sizeof(header)))throw HandlerError("invalid_memory","Стек недоступен");
        if(header.count<0 || header.capacity<header.count ||
           (!header.data && (header.count || header.capacity)) ||
           (header.data && (!header.capacity || header.data%alignof(StringHeader))))
            throw HandlerError("invalid_array_header","Некорректный массив стека");
        const auto capacity_bytes=static_cast<uintptr_t>(header.capacity)*sizeof(StringHeader);
        if(header.data>std::numeric_limits<uintptr_t>::max()-capacity_bytes)
            throw HandlerError("invalid_array_header","Адресный диапазон стека переполняется");
        frames.reserve(static_cast<size_t>(header.count));
        for(int32_t index=0;index<header.count;++index) {
            StringHeader frame{};
            if(!copy_memory(&frame,reinterpret_cast<const void*>(header.data+static_cast<uintptr_t>(index)*sizeof(frame)),sizeof(frame)))
                throw HandlerError("invalid_memory","Кадр стека недоступен");
            frames.push_back(copied_string(frame));
        }
        complete=true;
    } catch(...) {}
    as_exception_capture::record_stack(std::move(frames),complete);
}
void __fastcall GameAccess::hooked_tick(void* loop) {
    static std::atomic<int> reports{0};auto count=reports.fetch_add(1);
    if(count<2)diagnostic("tick_enter");
    auto self=active_; if(!self) return;
    self->original_tick_(loop);
    if(count<2)diagnostic("tick_original_return");
    if(self->enabled_) self->pump();
    if(count<2)diagnostic("tick_pump_return");
}
json GameAccess::status() const {
    const auto observed=last_tick_ms_.load(), now=steady_ms();
    const auto age=observed && now>=observed ? now-observed : 0;
    const bool recent=observed && age<=kRequestWaitMs;
    json pending=nullptr, active=nullptr;bool queue_metadata_available{};
    {
        std::unique_lock lock(queue_mutex_,std::try_to_lock);
        queue_metadata_available=lock.owns_lock();
        if(queue_metadata_available)pending=queue_.size();
        if(queue_metadata_available && active_job_)active={{"request_id",active_job_->request.value("id",json(nullptr))},
                              {"operation",active_job_->request.value("op",std::string{})},
                              {"state","running"}};
    }
    return {{"version","1.0.0-dev.49"},{"native_route","standalone_in_process"},{"hook_installed",enabled_.load()},
            {"as_exception_capture",{{"hook_installed",enabled_.load()},{"source","observed_logging_entry"},
                {"scope","current_thread_process_event"},{"historical_errors_available",false},{"stack_capture_available",enabled_.load()},{"stack_captured",false}}},
            {"game_thread_id",game_thread_.load()},{"tick_count",ticks_.load()},{"ready",enabled_.load()&&recent},
            {"pump",{{"state",!observed?"not_observed":recent?"recent_tick":"tick_stale"},
                     {"last_tick_monotonic_ms",observed?json(observed):json(nullptr)},
                     {"tick_age_ms",observed?json(age):json(nullptr)},
                     {"freshness_limit_ms",kRequestWaitMs},{"world_ready_confirmed",false}}},
            {"requests",{{"queue_depth",pending},{"queue_limit",8},{"active",active},{"metadata_available",queue_metadata_available},
                         {"completed",completed_jobs_.load()},{"cancelled_before_start",cancelled_jobs_.load()},
                         {"wait_timeout_ms",kRequestWaitMs},{"cancelled_jobs_can_execute",false}}},
            {"build_hash",kHash},{"startup_error",startup_error_},
            {"process_id",GetCurrentProcessId()},
            {"string_inputs",{{"quarantined",string_inputs_quarantined_.load()},
                {"quarantine_cause",string_inputs_quarantine_cause_.load()},
                {"retained_allocations",string_inputs_retained_count_.load()},
                {"retained_bytes",string_inputs_retained_bytes_.load()},
                {"max_inputs",nullptr},{"max_utf16_units",string_arguments::max_utf16_units},{"max_input_bytes",nullptr}}},
            {"object_array_inputs",{{"quarantined",object_array_inputs_quarantined_.load()},
                {"quarantine_cause",object_array_inputs_quarantine_cause_.load()},
                {"retained_allocations",object_array_inputs_retained_count_.load()},
                {"retained_bytes",object_array_inputs_retained_bytes_.load()},
                {"max_inputs",nullptr},{"max_elements",std::numeric_limits<int32_t>::max()},{"max_input_bytes",nullptr}}},
            {"operations",{"status","enable_game","compile_diagnostics","objects","properties","functions","invoke","read_array","read_struct",
                           "snapshot","watch_start","watch_read","watch_stop","watch_forget"}},
            {"snapshot_limits",{{"objects",16},{"properties_per_object",32},{"scan_fields_per_object",4096},
                                {"property_value_bytes",16384},{"object_path_bytes",8192},
                                {"snapshot_bytes",Observations::MaxSnapshotBytes}}},
            {"watch_limits",{{"records",Observations::MaxWatches},{"properties",32},
                             {"duration_ms_min",1},{"duration_ms_max",600000},
                             {"interval_ms_min",10},{"interval_ms_max",60000},
                             {"capacity_min",1},{"capacity_max",128},
                             {"buffer_bytes",Observations::MaxBufferBytes},{"read_events",100},
                             {"due_watches_per_tick",1}}},
            {"snapshot_consistency","single_tick_sequential"},
            {"world_generation_supported",false},
            {"serial_guard",{{"policy","strict_observed_equality_including_zero"},
                              {"checks",{"object_serial","class_identity","class_serial"}},
                              {"coverage","conditional_on_observed_nonzero_object_and_class_serial"},
                              {"allocates_serial",false},{"zero_aba_protection",false}}},
            {"limitations",{"Экспериментальная привязка к одному EXE","Нулевые serial не защищают от ABA; появившийся serial отзывает прежнюю ссылку","Одно активное соединение; ссылки и наблюдения принадлежат его сессии","Смена мира не определяет отдельное поколение; проверяется объект перед каждым чтением","Без выгрузки DLL: завершение через закрытие игры"}}};
}
json GameAccess::compile_diagnostics() {
    const auto image_base=ensure_image_base();
    const auto manager=read<uintptr_t>(image_base+kAngelscriptManager);
    if(!manager)throw HandlerError("compile_diagnostics_unavailable","Менеджер AngelScript ещё не создан");
    auto* compilation_lock=reinterpret_cast<CRITICAL_SECTION*>(manager+0x390);
    std::string text;
    EnterCriticalSection(compilation_lock);
    try {
        StringHeader header{};
        using Formatter=StringHeader*(__fastcall*)(void*,StringHeader*);
        reinterpret_cast<Formatter>(image_base+kFormatDiagnostics)(reinterpret_cast<void*>(manager),&header);
        text=copied_owned_string_return(header,[](const StringHeader& value) {
            std::wstring copied(static_cast<size_t>(value.count),L'\0');
            const auto bytes=static_cast<size_t>(value.count)*sizeof(wchar_t);
            size_t offset=0;
            while(offset<bytes) {
                const auto count=std::min<size_t>(bytes-offset,1048576);
                if(!copy_memory(reinterpret_cast<std::byte*>(copied.data())+offset,
                                reinterpret_cast<const void*>(value.data+offset),count))
                    throw HandlerError("invalid_memory","Текст диагностики недоступен");
                offset+=count;
            }
            if(!copied.empty() && copied.back()==0)copied.pop_back();
            return utf8(copied.data(),static_cast<int>(copied.size()));
        },[image_base](uintptr_t data) {
            return free_string(image_base+kFree,reinterpret_cast<void*>(data));
        });
        LeaveCriticalSection(compilation_lock);
    } catch(...) {
        LeaveCriticalSection(compilation_lock);
        throw;
    }
    return {{"status","available"},{"source","FAngelscriptManager::FormatDiagnostics"},
            {"process_id",GetCurrentProcessId()},{"text",std::move(text)}};
}
json GameAccess::request(const json& envelope) {
    if(envelope.at("op")=="status") return status();
    if(envelope.at("op")=="compile_diagnostics") return compile_diagnostics();
    if(!enabled_) throw HandlerError("native_not_ready","Перехват игры не установлен: "+startup_error_);
    auto job=std::make_shared<Job>();job->request=envelope;auto future=job->result.get_future();
    {std::unique_lock lock(queue_mutex_,std::try_to_lock);
     if(!lock.owns_lock())throw HandlerError("game_thread_busy","Очередь занята обслуживанием игрового потока; запрос не поставлен",
        {{"executed",false},{"may_have_executed",false},{"do_not_retry",false},{"request_started",false},{"stage","not_queued"}});
     if(queue_.size()>=8)throw HandlerError("queue_full","Очередь игрового потока заполнена");queue_.push_back(job);}
    if(future.wait_for(std::chrono::milliseconds(kRequestWaitMs))!=std::future_status::ready) {
        if(job->state.cancel()) {
            ++cancelled_jobs_;
            {std::unique_lock lock(queue_mutex_,std::try_to_lock);if(lock.owns_lock()){auto entry=std::find(queue_.begin(),queue_.end(),job);if(entry!=queue_.end())queue_.erase(entry);}}
            throw HandlerError("game_not_pumping","Запрос отменён до начала: игровой Tick не обслужил очередь",
                {{"executed",false},{"may_have_executed",false},{"do_not_retry",false},
                 {"request_started",false},{"stage","queued_cancelled"},{"native_status",status()}});
        }
        if(future.wait_for(std::chrono::milliseconds(0))==std::future_status::ready)return future.get();
        if(job->state.get()==JobState::cancelled)throw HandlerError("session_closed","Запрос отменён до исполнения после закрытия сессии",
            {{"executed",false},{"may_have_executed",false},{"do_not_retry",false},{"request_started",false},{"stage","queued_cancelled"}});
        throw HandlerError("game_timeout","Ожидание закончено; принятый вызов мог выполниться",
            {{"executed",false},{"may_have_executed",true},{"do_not_retry",true},
             {"request_started",true},{"stage","running_outcome_unknown"},{"native_status",status()}});
    }
    return future.get();
}
void GameAccess::disconnect(const std::string& session) {
    // Сетевой поток: только пометка и отмена ещё не вошедших в Tick работ.
    std::lock_guard lock(queue_mutex_);
    session_cleanup_pending_=true;
    for (const auto& job : queue_) {
        if (job->request.value("session_id",std::string{})!=session) continue;
        if (job->state.cancel()) {
            ++cancelled_jobs_;
            job->result.set_exception(std::make_exception_ptr(
                HandlerError("session_closed","Сессия отключилась до исполнения запроса")));
        }
    }
}
void GameAccess::pump() {
    if(!game_thread_.load()) game_thread_=GetCurrentThreadId();
    if(game_thread_.load()!=GetCurrentThreadId()) return;
    ++ticks_;
    last_tick_ms_=steady_ms();
    std::shared_ptr<Job> job;
    {
        std::lock_guard lock(queue_mutex_);
        if(session_cleanup_pending_) {
            observations_.disconnect(handle_session_);
            handles_.clear();signature_contracts_.clear();handle_session_.clear();session_cleanup_pending_=false;
        }
        if(!queue_.empty()){job=queue_.front();queue_.pop_front();}
    }
    if(job) {
        if(job->state.begin()) {
            {std::lock_guard lock(queue_mutex_);active_job_=job;}
            try {job->result.set_value(execute(job->request));}catch(...) {job->result.set_exception(std::current_exception());}
            job->state.finish();++completed_jobs_;
            {std::lock_guard lock(queue_mutex_);active_job_.reset();}
        }
    }
    const auto now=static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count());
    // Уведомление disconnect ждёт уже вошедшее чтение; после пометки новая
    // выборка не начинается. Реальная очистка выполняется тем же Tick.
    {
        std::lock_guard lock(queue_mutex_);
        if(session_cleanup_pending_) {
            observations_.disconnect(handle_session_);
            handles_.clear();signature_contracts_.clear();handle_session_.clear();session_cleanup_pending_=false;
        } else {
            observations_.pump(now,ticks_.load(),[this](const json& targets){return snapshot(targets);});
        }
    }
}

std::string GameAccess::name(uint64_t fname) const {
    uint32_t index=static_cast<uint32_t>(fname),number=static_cast<uint32_t>(fname>>32);
    uint32_t block=index>>16;
    auto current=read<uint32_t>(base_+kNames+8);
    if(block>current || block>=8192) throw HandlerError("invalid_name","Недействительное имя");
    auto data=read<uintptr_t>(base_+kNames+0x10+block*8ull)+(index&65535)*2ull;
    auto header=read<uint16_t>(data);int length=header>>6;
    if(length<1 || length>1023 || (index&65535)*2ull+2+length*((header&1)?2:1)>0x20000) throw HandlerError("invalid_name","Недействительная запись имени");
    std::string result;
    if(header&1) {std::vector<wchar_t> text(length);if(!copy_memory(text.data(),reinterpret_cast<void*>(data+2),length*2ull))throw HandlerError("invalid_memory","Имя недоступно");result=utf8(text.data(),length);}
    else {std::string bytes(length,'\0');if(!copy_memory(bytes.data(),reinterpret_cast<void*>(data+2),length))throw HandlerError("invalid_memory","Имя недоступно");
        std::wstring text; for(unsigned char byte:bytes)text.push_back(static_cast<wchar_t>(byte));result=utf8(text.data(),static_cast<int>(text.size()));}
    if(number) result+="_"+std::to_string(number-1);return result;
}
std::string GameAccess::object_name(void* object) const {return name(read<uint64_t>(reinterpret_cast<uintptr_t>(object)+0x18));}
std::string GameAccess::object_path(void* object) const {
    std::string result;auto ptr=reinterpret_cast<uintptr_t>(object);
    for(int depth=0;ptr && depth<32;++depth) {auto part=object_name(reinterpret_cast<void*>(ptr));result=result.empty()?part:part+"."+result;ptr=read<uintptr_t>(ptr+0x20);}return result;
}
GameAccess::SlotIdentity GameAccess::slot_identity(int index) const {
    auto count=read<int32_t>(base_+kObjects+0x14);auto chunks=read<uintptr_t>(base_+kObjects);
    if(count<0 || count>4000000 || index<0 || index>=count)return {};
    auto chunk_count=read<int32_t>(base_+kObjects+0x1c);
    if((index>>16)>=chunk_count || chunk_count<1 || chunk_count>64 || !chunks)return {};
    auto chunk=read<uintptr_t>(chunks+(index>>16)*8ull);
    if(!chunk)return {};
    const auto item=chunk+(index&65535)*24ull;
    const auto object=read<uintptr_t>(item);
    if(!object)return {};
    const auto serial=read<int32_t>(item+0x10);
    if(!valid_observed_serial(serial))throw HandlerError("unsupported_serial_value","Наблюдаемый serial слота не поддержан");
    if(read<uintptr_t>(item)!=object || read<int32_t>(item+0x10)!=serial)
        throw HandlerError("stale_handle","Identity слота изменилась при чтении; получите новую ссылку");
    return {reinterpret_cast<void*>(object),serial};
}
void* GameAccess::slot(int index) const {
    return slot_identity(index).object;
}
ObjectSerialIdentity GameAccess::serial_identity(const Handle& value) {
    return {reinterpret_cast<uintptr_t>(value.object),reinterpret_cast<uintptr_t>(value.object_class),
            value.index,value.name,value.serial,value.class_index,value.class_name,value.class_serial};
}
std::string GameAccess::handle_for(void* object,int index) {
    const auto identity=validate_object(object);
    if(identity.index!=index)throw HandlerError("stale_handle","Индекс объекта изменился");
    for(const auto& [id,existing]:handles_) {
        if(same_serial_identity(serial_identity(existing),serial_identity(identity)))return id;
    }
    if(handles_.size()>=4096)throw HandlerError("handle_limit","Реестр ссылок заполнен; существующие ссылки сохранены");
    auto id="obj-"+std::to_string(++next_handle_);
    handles_[id]=identity;return id;
}
GameAccess::Handle GameAccess::validate_object(void* object) const {
    if(!object)throw HandlerError("invalid_object","Указатель объекта отсутствует");
    const auto ptr=reinterpret_cast<uintptr_t>(object);
    const auto index=read<int32_t>(ptr+0xc);
    const auto item=slot_identity(index);
    if(index<0 || item.object!=object)throw HandlerError("stale_handle","Объект не соответствует слоту реестра");
    if(read<uint32_t>(ptr+8)&0x18000)throw HandlerError("object_destroying","Объект уничтожается");
    const auto object_class=read<uintptr_t>(ptr+0x10);
    if(!object_class)throw HandlerError("invalid_object_class","У объекта отсутствует класс");
    const auto class_index=read<int32_t>(object_class+0xc);
    const auto class_item=slot_identity(class_index);
    if(class_index<0 || class_item.object!=reinterpret_cast<void*>(object_class)
       || (read<uint32_t>(object_class+8)&0x18000))
        throw HandlerError("invalid_object_class","Класс объекта не соответствует живому слоту");
    const auto object_fname=read<uint64_t>(ptr+0x18);
    const auto class_fname=read<uint64_t>(object_class+0x18);
    name(object_fname);name(class_fname);
    const auto item_after=slot_identity(index),class_after=slot_identity(class_index);
    if(item_after.object!=object || item_after.serial!=item.serial
       || class_after.object!=reinterpret_cast<void*>(object_class) || class_after.serial!=class_item.serial
       || read<int32_t>(ptr+0xc)!=index || read<uintptr_t>(ptr+0x10)!=object_class
       || read<uint64_t>(ptr+0x18)!=object_fname || read<int32_t>(object_class+0xc)!=class_index
       || read<uint64_t>(object_class+0x18)!=class_fname)
        throw HandlerError("stale_handle","Identity объекта или класса изменилась при чтении; получите новую ссылку");
    if((read<uint32_t>(ptr+8)|read<uint32_t>(object_class+8))&0x18000)
        throw HandlerError("object_destroying","Объект или класс уничтожается");
    return {object,reinterpret_cast<void*>(object_class),index,object_fname,item.serial,class_index,class_fname,class_item.serial};
}
void GameAccess::validate_class(void* object_class) const {
    // Точная identity metaclass подтверждена read-only в текущей игре:
    // /Script/CoreUObject.Class (index 15, class Class), 30.10.2026.
    const auto identity=validate_object(object_class);
    auto metaclass=reinterpret_cast<uintptr_t>(identity.object_class);
    std::set<uintptr_t> visited;
    for(int depth=0;metaclass && depth<64;++depth) {
        if(!visited.insert(metaclass).second)
            throw HandlerError("invalid_object_class","Цикл метакласса");
        validate_object(reinterpret_cast<void*>(metaclass));
        if(object_path(reinterpret_cast<void*>(metaclass))=="/Script/CoreUObject.Class")return;
        metaclass=read<uintptr_t>(metaclass+0x40);
    }
    throw HandlerError("invalid_object_class","Объект PropertyClass не является подтверждённым UClass");
}
bool GameAccess::is_compatible_object(void* object,void* expected_class) const {
    const auto identity=validate_object(object);
    validate_class(expected_class);
    validate_class(identity.object_class);
    return compatible_object_class(reinterpret_cast<uintptr_t>(identity.object_class),
        reinterpret_cast<uintptr_t>(expected_class),[this](uintptr_t klass) {
            validate_class(reinterpret_cast<void*>(klass));
            const auto parent=read<uintptr_t>(klass+0x40);
            if(parent)validate_class(reinterpret_cast<void*>(parent));
            return parent;
        });
}
bool GameAccess::is_compatible_class(void* represented_class,void* property_class,void* meta_class) const {
    validate_class(represented_class);
    validate_class(property_class);
    validate_class(meta_class);
    const auto identity=validate_object(represented_class);
    return compatible_class_argument(reinterpret_cast<uintptr_t>(represented_class),
        reinterpret_cast<uintptr_t>(identity.object_class),reinterpret_cast<uintptr_t>(property_class),
        reinterpret_cast<uintptr_t>(meta_class),[this](uintptr_t klass) {
            validate_class(reinterpret_cast<void*>(klass));
            const auto parent=read<uintptr_t>(klass+0x40);
            if(parent)validate_class(reinterpret_cast<void*>(parent));
            return parent;
        });
}
json GameAccess::object_descriptor(void* object,void* expected_class) {
    validate_class(expected_class);
    if(!object)return nullptr;
    if(!is_compatible_object(object,expected_class))
        throw HandlerError("incompatible_object_result","Объект не принадлежит объявленному классу свойства");
    const auto identity=validate_object(object);
    const auto path=object_path(object);
    if(path.size()>8192)throw HandlerError("object_identity_too_large","Путь объектного значения превышает 8 КиБ");
    json result={{"handle",handle_for(object,identity.index)},{"index",identity.index},
        {"name",object_name(object)},{"path",path},{"class",object_name(identity.object_class)},
        {"session_id",handle_session_}};
    const auto after=validate_object(object);
    if(!same_serial_identity(serial_identity(identity),serial_identity(after)))
        throw HandlerError("stale_handle","Объектное значение изменилось при копировании");
    return result;
}
json GameAccess::class_descriptor(void* object,void* property_class,void* meta_class) {
    validate_class(property_class);
    validate_class(meta_class);
    if(!object)return nullptr;
    validate_class(object);
    if(!is_compatible_class(object,property_class,meta_class))
        throw HandlerError("incompatible_class_result","UClass не соответствует PropertyClass или MetaClass");
    auto result=object_descriptor(object,property_class);
    if(!is_compatible_class(object,property_class,meta_class))
        throw HandlerError("incompatible_class_result","Классовое значение изменилось при копировании");
    return result;
}
json GameAccess::property_value(uintptr_t address,const std::string& type,int size,uintptr_t field) {
    if(type=="ClassProperty") {
        if(size!=8)throw HandlerError("unsupported_property_layout","ClassProperty требует указатель x64");
        return class_descriptor(reinterpret_cast<void*>(read<uintptr_t>(address)),
            reinterpret_cast<void*>(read<uintptr_t>(field+0x70)),reinterpret_cast<void*>(read<uintptr_t>(field+0x78)));
    }
    if(type=="ObjectProperty") {
        if(size!=sizeof(uintptr_t))throw HandlerError("unsupported_property_layout","ObjectProperty требует указатель x64");
        const auto expected_class=reinterpret_cast<void*>(read<uintptr_t>(field+0x70));
        return object_descriptor(reinterpret_cast<void*>(read<uintptr_t>(address)),expected_class);
    }
    if(type=="NameProperty") {
        if(size!=8)throw HandlerError("unsupported_property_layout","NameProperty требует FName размером 8 байт");
        return name(read<uint64_t>(address));
    }
    if(type=="EnumProperty" || type=="ByteProperty")return enum_property(address,type,size,field).at("value");
    return scalar(address,type,size,field);
}
GameAccess::EnumTable GameAccess::enum_table(void* object) const {
    if(!object)throw HandlerError("enum_validation_unavailable","UEnum для проверки аргумента отсутствует");
    try {
        const auto identity=validate_object(object);
        const auto enum_class=reinterpret_cast<uintptr_t>(identity.object_class);
        validate_class(identity.object_class);
        // Root read-only probe 31.10.2026 подтвердил /Script/CoreUObject.Enum:
        // index 19, name Enum, class Class; UserDefinedEnum принимает наследование.
        bool valid_enum=false;
        std::set<uintptr_t> visited;
        auto current=enum_class;
        for(int depth=0;current && depth<64;++depth) {
            if(!visited.insert(current).second)throw HandlerError("invalid_enum_class","Цикл класса UEnum");
            validate_class(reinterpret_cast<void*>(current));
            if(object_path(reinterpret_cast<void*>(current))=="/Script/CoreUObject.Enum"){valid_enum=true;break;}
            current=read<uintptr_t>(current+0x40);
        }
        if(!valid_enum)throw HandlerError("invalid_enum_class","Объявление enum не является UEnum");
        const auto path=object_path(object);
        if(path.size()>8192)throw HandlerError("enum_path_too_large","Путь enum превышает 8 КиБ");
        const auto ptr=reinterpret_cast<uintptr_t>(object);
        const auto data=read<uintptr_t>(ptr+0x40);
        const auto count=read<int32_t>(ptr+0x48),capacity=read<int32_t>(ptr+0x4c);
        const auto bytes=g1r::enum_values::table_byte_size(count,capacity,data!=0);
        std::vector<std::byte> copied(bytes);
        if(bytes && !copy_memory(copied.data(),reinterpret_cast<void*>(data),bytes))
            throw HandlerError("invalid_enum_table","Таблица enum недоступна");
        if(read<uintptr_t>(ptr+0x40)!=data || read<int32_t>(ptr+0x48)!=count
           || read<int32_t>(ptr+0x4c)!=capacity)
            throw HandlerError("enum_table_changed","Таблица enum изменилась при копировании");
        const auto after=validate_object(object);
        if(!same_serial_identity(serial_identity(identity),serial_identity(after)))
            throw HandlerError("stale_handle","Enum изменился при копировании");
        auto entries=g1r::enum_values::decode_entries(copied,count,capacity);
        return {identity,path,std::move(entries)};
    } catch(const std::invalid_argument& error) {
        throw HandlerError("enum_validation_unavailable",error.what());
    }
}
json GameAccess::enum_metadata(void* object,const json& value,const std::string& underlying_type) {
    json metadata={{"path",nullptr},{"underlying_type",underlying_type},{"name_status","unavailable"},
        {"value_name",nullptr},{"matched_count",0},{"ambiguous",false},{"reason","enum_unavailable"}};
    if(!object){metadata["reason"]="enum_pointer_null";return metadata;}
    try {
        const auto table=enum_table(object);
        metadata["path"]=table.path;
        const g1r::enum_values::Integer number=value.is_number_unsigned()
            ?g1r::enum_values::Integer(value.get<uint64_t>()):g1r::enum_values::Integer(value.get<int64_t>());
        const auto match=g1r::enum_values::find_label(number,table.entries,[this](uint64_t fname){return name(fname);});
        metadata["name_status"]=match.status;
        metadata["value_name"]=match.name.empty()?json(nullptr):json(match.name);
        metadata["matched_count"]=match.matched_count;metadata["ambiguous"]=match.ambiguous;
        metadata["reason"]=match.reason.empty()?json(nullptr):json(match.reason);
    } catch(const HandlerError& error) {metadata["reason"]=error.code();}
      catch(const std::invalid_argument& error) {metadata["reason"]=error.what();}
    return metadata;
}
GameAccess::EnumLayout GameAccess::enum_layout(const std::string& type,int size,uintptr_t field) const {
    try {
        if(field_type(field,[this](auto f){return name(f);})!=type
           || read<int32_t>(field+0x34)!=size || read<int32_t>(field+0x30)!=1)
            throw HandlerError("unsupported_enum_underlying_layout","Внешняя раскладка enum изменилась или не поддержана");
        std::string underlying_type="ByteProperty";
        uintptr_t enum_ptr=0;
        if(type=="EnumProperty") {
            const auto underlying=read<uintptr_t>(field+0x70);
            if(!underlying)throw HandlerError("unsupported_enum_underlying_layout","UnderlyingProp отсутствует");
            underlying_type=field_type(underlying,[this](auto f){return name(f);});
            if(read<int32_t>(underlying+0x30)!=1 || read<int32_t>(underlying+0x34)!=size)
                throw HandlerError("unsupported_enum_underlying_layout","Размер/массив UnderlyingProp не совпадает");
            enum_ptr=read<uintptr_t>(field+0x78);
        } else enum_ptr=read<uintptr_t>(field+0x70);
        g1r::enum_values::integer_layout(underlying_type,size);
        return {underlying_type,size,reinterpret_cast<void*>(enum_ptr)};
    } catch(const std::invalid_argument& error) {
        throw HandlerError(error.what(),"Неподдерживаемая раскладка enum");
    }
}
json GameAccess::enum_property(uintptr_t address,const std::string& type,int size,uintptr_t field) {
    namespace ev=g1r::enum_values;
    try {
        const auto declared=enum_layout(type,size,field);
        const auto layout=ev::integer_layout(declared.underlying_type,size);
        std::array<std::byte,8> copied{};
        if(!copy_memory(copied.data(),reinterpret_cast<void*>(address),layout.bytes))throw HandlerError("invalid_memory","Число enum недоступно");
        const auto integer=ev::decode_integer(declared.underlying_type,size,std::span(copied).first(layout.bytes));
        auto value=std::visit([](auto number){return json(number);},integer);
        json result={{"value",value}};
        if(type=="EnumProperty" || declared.object)result["enum"]=enum_metadata(declared.object,value,declared.underlying_type);
        return result;
    } catch(const std::invalid_argument& error) {
        throw HandlerError(error.what(),"Неподдерживаемая раскладка enum");
    }
}
void* GameAccess::resolve(const std::string& id) {
    auto found=handles_.find(id);if(found==handles_.end())throw HandlerError("stale_handle","Ссылка неизвестна или истекла");
    const auto& h=found->second;
    const auto current=validate_object(h.object);
    if(!same_serial_identity(serial_identity(h),serial_identity(current)))
        throw HandlerError("stale_handle","Наблюдаемая identity объекта или класса изменилась; получите новую ссылку");
    return h.object;
}
json GameAccess::object_info(void* object,int index) {
    auto ptr=reinterpret_cast<uintptr_t>(object);return {{"handle",handle_for(object,index)},{"index",index},{"name",object_name(object)},
        {"path",object_path(object)},{"class",object_name(reinterpret_cast<void*>(read<uintptr_t>(ptr+0x10)))},{"flags",read<uint32_t>(ptr+8)}};
}
json GameAccess::properties(void* object,int limit) {
    json items=json::array();auto klass=read<uintptr_t>(reinterpret_cast<uintptr_t>(object)+0x10);bool truncated=false;
    for(int depth=0;klass && depth<64;klass=read<uintptr_t>(klass+0x40),++depth) {
        auto field=read<uintptr_t>(klass+0x50);auto class_size=read<int32_t>(klass+0x58);
        for(int count=0;field && count<4096;field=read<uintptr_t>(field+0x18),++count) {
            if(items.size()>=static_cast<size_t>(limit)){truncated=true;break;}
            auto type=field_type(field,[this](auto f){return name(f);});auto offset=read<int32_t>(field+0x44),size=read<int32_t>(field+0x34);
            json item={{"name",name(read<uint64_t>(field+0x20))},{"type",type},{"offset",offset},{"element_size",size},{"array_dim",read<int32_t>(field+0x30)}};
            if(offset>=0 && size>0 && offset<=class_size-size && item["array_dim"]==1) {
                try {
                    json value;
                    if(type=="EnumProperty" || type=="ByteProperty") {
                        const auto result=enum_property(reinterpret_cast<uintptr_t>(object)+offset,type,size,field);
                        value=result.at("value");if(result.contains("enum"))item["enum"]=result.at("enum");
                    } else value=property_value(reinterpret_cast<uintptr_t>(object)+offset,type,size,field);
                    item["value"]=value;item["supported"]=type=="ObjectProperty" || type=="ClassProperty" || !value.is_null();
                } catch(const HandlerError& error) {
                    item["supported"]=false;item["error"]={{"code",error.code()},{"message",error.what()}};
                }
            } else {item["supported"]=false;}
            items.push_back(item);
        } if(truncated)break;
    }
    return {{"items",items},{"truncated",truncated},{"validation","game_thread_memory_read"}};
}
json GameAccess::selected_properties(void* object,const json& names) {
    json items=json::array();
    std::unordered_map<std::string,json> selected;
    auto klass=read<uintptr_t>(reinterpret_cast<uintptr_t>(object)+0x10);
    int scanned=0;bool scan_truncated=false;
    std::set<uintptr_t> visited_classes,visited_fields;
    for(int depth=0;klass && depth<64 && selected.size()<names.size();
        klass=read<uintptr_t>(klass+0x40),++depth) {
        if(!visited_classes.insert(klass).second){scan_truncated=true;break;}
        auto field=read<uintptr_t>(klass+0x50);auto class_size=read<int32_t>(klass+0x58);
        for(;field && selected.size()<names.size();field=read<uintptr_t>(field+0x18)) {
            if(scanned==4096 || !visited_fields.insert(field).second){scan_truncated=true;break;}
            ++scanned;
            const auto field_name=name(read<uint64_t>(field+0x20));
            if(selected.contains(field_name) || std::find(names.begin(),names.end(),json(field_name))==names.end())continue;
            const auto type=field_type(field,[this](auto f){return name(f);});
            const auto offset=read<int32_t>(field+0x44),size=read<int32_t>(field+0x34);
            json item={{"name",field_name},{"type",type},{"supported",false}};
            try {
                if(offset<0 || size<=0 || size>class_size || offset>class_size-size
                   || read<int32_t>(field+0x30)!=1)
                    throw HandlerError("unsupported_property_layout","Неподдерживаемая раскладка свойства");
                json value;
                if(type=="EnumProperty" || type=="ByteProperty") {
                    const auto result=enum_property(reinterpret_cast<uintptr_t>(object)+offset,type,size,field);
                    value=result.at("value");if(result.contains("enum"))item["enum"]=result.at("enum");
                } else value=property_value(reinterpret_cast<uintptr_t>(object)+offset,type,size,field);
                if(value.is_null() && type!="ObjectProperty" && type!="ClassProperty")throw HandlerError("unsupported_property_type","Тип свойства не поддержан");
                if(value.dump().size()>16384)throw HandlerError("property_value_too_large","Значение превышает предел поля 16 КиБ");
                item["value"]=std::move(value);item["supported"]=true;
            } catch(const HandlerError& error) {
                item["error"]={{"code",error.code()},{"message",error.what()}};
            }
            selected.emplace(field_name,std::move(item));
        }
        if(scan_truncated)break;
        if(depth==63 && selected.size()<names.size() && read<uintptr_t>(klass+0x40))scan_truncated=true;
    }
    bool partial=scan_truncated;
    for(const auto& requested:names) {
        const auto key=requested.get<std::string>();
        auto found=selected.find(key);
        if(found==selected.end()) {
            items.push_back({{"name",key},{"type",nullptr},{"supported",false},
                {"error",{{"code",scan_truncated?"property_scan_truncated":"property_not_found"},
                           {"message",scan_truncated?"Поиск свойства достиг лимита":"Свойство не найдено"}}}});
            partial=true;
        } else {
            if(!found->second.at("supported").get<bool>())partial=true;
            items.push_back(found->second);
        }
    }
    return {{"properties",items},{"partial",partial},{"truncated",scan_truncated},{"scanned_fields",scanned}};
}
json GameAccess::snapshot(const json& targets) {
    Observations::validate_targets(targets);
    json result={{"snapshot_id","snapshot-"+std::to_string(++next_snapshot_)},
        {"source","live_native"},{"session_id",handle_session_},{"process_id",GetCurrentProcessId()},
        {"thread_id",GetCurrentThreadId()},{"tick_count",ticks_.load()},
        {"consistency","single_tick_sequential"},{"objects",json::array()},
        {"partial",false},{"truncated",false},
        {"limits",{{"objects",16},{"properties_per_object",32},{"scan_fields_per_object",4096},
                   {"property_value_bytes",16384},{"snapshot_bytes",Observations::MaxSnapshotBytes}}}};
    for(const auto& target:targets) {
        const auto handle=target.at("handle").get<std::string>();
        json item={{"handle",handle},{"properties",json::array()}};
        try {
            auto object=resolve(handle);
            item["path"]=object_path(object);
            item["class"]=object_name(reinterpret_cast<void*>(read<uintptr_t>(reinterpret_cast<uintptr_t>(object)+0x10)));
            if(item["path"].get_ref<const std::string&>().size()>8192)
                throw HandlerError("object_identity_too_large","Путь объекта превышает предел 8 КиБ");
            const auto fields=selected_properties(object,target.at("properties"));
            resolve(handle);
            item["properties"]=fields.at("properties");
            item["partial"]=fields.at("partial");item["truncated"]=fields.at("truncated");
            if(fields.at("partial").get<bool>())result["partial"]=true;
            if(fields.at("truncated").get<bool>())result["truncated"]=true;
        } catch(const HandlerError& error) {
            item["properties"]=json::array();
            if(error.code()=="object_identity_too_large")item.erase("path");
            item["error"]={{"code",error.code()},{"message",error.what()}};
            result["partial"]=true;
        }
        if(result.dump().size()+item.dump().size()>Observations::MaxSnapshotBytes-4096) {
            result["objects"].push_back({{"handle",handle},{"properties",json::array()},
                {"error",{{"code","snapshot_too_large"},{"message","Снимок достиг предела 256 КиБ"}}}});
            result["partial"]=true;result["truncated"]=true;
            break;
        }
        result["objects"].push_back(std::move(item));
    }
    return result;
}
json GameAccess::capture_function_contract(void* object,uintptr_t owner,uintptr_t function,const std::string& target_handle) {
    auto capture_scalar_struct=[this](uintptr_t field,int size,int dim) {
        return scalar_struct_values::capture(field,size,dim,
            []<class Value>(uintptr_t address){return read<Value>(address);},
            [this](uint64_t value){return name(value);},
            [this](uintptr_t value){return serial_identity(validate_object(reinterpret_cast<void*>(value)));},
            [this](uintptr_t value){validate_class(reinterpret_cast<void*>(value));},
            [this](uintptr_t value){return object_path(reinterpret_cast<void*>(value));});
    };
    auto identity=[](const Handle& value) {
        const auto observed=serial_identity(value);
        return json{{"object",observed.object},{"object_class",observed.object_class},
            {"index",observed.index},{"name",observed.name},{"serial",observed.serial},
            {"class_index",observed.class_index},{"class_name",observed.class_name},{"class_serial",observed.class_serial}};
    };
    if(resolve(target_handle)!=object)throw HandlerError("signature_token_mismatch","Ссылка сигнатуры относится к другому объекту");
    validate_class(reinterpret_cast<void*>(owner));
    const auto object_identity=validate_object(object),owner_identity=validate_object(reinterpret_cast<void*>(owner));
    const auto function_identity=validate_object(reinterpret_cast<void*>(function));
    if(!contains(object_name(function_identity.object_class),"Function"))
        throw HandlerError("unsupported_signature","Запись отражения не является UFunction");
    const auto function_name=object_name(reinterpret_cast<void*>(function));
    const auto field_head=read<uintptr_t>(function+0x50);
    const auto function_flags=read<uint32_t>(function+0xb0);
    const auto declared_count=read<uint8_t>(function+0xb4);
    const auto parameter_bytes=read<uint16_t>(function+0xb6);
    const auto return_offset=read<uint16_t>(function+0xb8);
    json parameters=json::array();int count{},returns{},extent=parameter_bytes;
    const auto fields=capture_function_field_chain(field_head,[&](uintptr_t field) {
        const auto field_class=read<uintptr_t>(field+8),field_name=read<uint64_t>(field+0x20);
        const auto type_name=read<uint64_t>(field_class);
        const auto type=name(type_name);const auto flags=read<uint64_t>(field+0x38);
        const auto offset=read<int32_t>(field+0x44),size=read<int32_t>(field+0x34),array_dim=read<int32_t>(field+0x30);
        json captured={{"pointer",field},{"next",read<uintptr_t>(field+0x18)},
            {"owner",read<uintptr_t>(field+0x10)},{"field_class",field_class},{"type_name",type_name},
            {"type",type},{"name",field_name},{"flags",flags},{"offset",offset},{"size",size},{"array_dim",array_dim}};
        if(!(flags&0x80))return captured;
        ++count;extent=parameter_extent(extent,offset,size,array_dim);
        const bool returned=(flags&0x400)!=0;
        if(returned && ++returns>1)throw HandlerError("unsupported_signature","У UFunction несколько возвратов");
        const bool scalar_output=!returned && supported_scalar_output_type(type) && (flags&0x100);
        json scalar_public=nullptr;
        if(scalar_output) {
            if(parameter_direction::classify(flags)!=parameter_direction::Direction::output)
                throw HandlerError("unsupported_signature","Скалярный параметр не является pure out");
            scalar_public=scalar_output_metadata(capture_scalar_output_layout(field,type,size,array_dim));
        } else if(!returned && type!="StructProperty" && type!="StrProperty" && type!="ArrayProperty" && (flags&(0x100|kReferenceParm)))
            throw HandlerError("unsupported_signature","Out/ref аргументы этого типа не поддержаны");
        if(returned && supported_scalar_output_type(type) && (flags&(kReferenceParm|kConstParm)))
            throw HandlerError("unsupported_signature","Const/ref скалярный возврат не поддержан");
        bool supported=false;
        json vector_public=nullptr,transform_public=nullptr,rotator_public=nullptr,array_public=nullptr,color_public=nullptr,scalar_struct_public=nullptr;std::string vector_direction;
        if(type=="BoolProperty") {
            const auto byte_offset=read<uint8_t>(field+0x71),mask=read<uint8_t>(field+0x73);
            validate_bool_layout(size,byte_offset,mask);
            captured["aux"]={{"field_size",read<uint8_t>(field+0x70)},{"byte_offset",byte_offset},
                {"byte_mask",read<uint8_t>(field+0x72)},{"field_mask",mask}};supported=true;
        } else if(type=="IntProperty")supported=size==4;
        else if(type=="FloatProperty")supported=size==4;
        else if(type=="DoubleProperty")supported=size==8;
        else if(type=="NameProperty")supported=size==8;
        else if(type=="StructProperty") {
            const auto structure_path=object_path(reinterpret_cast<void*>(read<uintptr_t>(field+0x70)));
            if(structure_path=="/Script/CoreUObject.LinearColor") {
                if(flags&(kConstParm|kReferenceParm) || (!returned && (flags&0x100)))
                    throw HandlerError("unsupported_signature","FLinearColor поддержан только по значению, без Const/Ref/Out");
                const auto color=capture_linear_color_parameter(field,size,array_dim);
                captured["aux"]={{"linear_color",linear_color_snapshot_json(color)}};
                color_public=linear_color_snapshot_json(color,false);supported=true;
            } else if(structure_path=="/Script/CoreUObject.Transform") {
                if(returned) {
                    if(flags&(kConstParm|kReferenceParm))
                        throw HandlerError("unsupported_signature","Const/ref возврат Transform не поддержан");
                } else if(vector_parameter_direction::classify(flags)!=vector_parameter_direction::Direction::input)
                    throw HandlerError("unsupported_signature","Transform требует вход по значению либо const reference; Out не поддержан");
                const auto transform=capture_transform_parameter(field,size,array_dim);
                if(offset%transform.layout.min_alignment)
                    throw HandlerError("unsupported_transform_layout","Смещение Transform не соответствует отражённому выравниванию");
                captured["aux"]={{"transform",transform_snapshot_json(transform)}};
                transform_public=transform_snapshot_json(transform,false);supported=true;
            } else if(structure_path=="/Script/CoreUObject.Vector") {
                if(!returned) {
                    const auto direction=vector_parameter_direction::classify(flags);
                    vector_direction=direction==vector_parameter_direction::Direction::output?"output":"input";
                } else {
                    if(flags&(kReferenceParm|kConstParm))
                        throw HandlerError("unsupported_signature","Const/ref возврат FVector не поддержан");
                    vector_direction="return";
                }
                const auto vector=capture_vector_parameter(field,size,array_dim);
                captured["aux"]={{"vector",vector_snapshot_json(vector)}};
                vector_public=vector_snapshot_json(vector,false);supported=true;
            } else if(structure_path=="/Script/CoreUObject.Rotator") {
                if(returned) {
                    if(flags&(kConstParm|kReferenceParm))
                        throw HandlerError("unsupported_signature","Const/ref возврат FRotator не поддержан");
                } else if(vector_parameter_direction::classify(flags)!=vector_parameter_direction::Direction::input)
                    throw HandlerError("unsupported_signature","FRotator требует вход по значению либо const reference; mutable Out не поддержан");
                const auto rotator=capture_scalar_struct(field,size,array_dim);
                rotator_arguments::validate(rotator);
                if(offset%rotator.layout.min_alignment)
                    throw HandlerError("unsupported_rotator_layout","Смещение FRotator не соответствует отражённому выравниванию");
                captured["aux"]={{"rotator",scalar_struct_snapshot_json(rotator)}};
                rotator_public=scalar_struct_snapshot_json(rotator,false);supported=true;
            } else {
                if(!returned || (flags&(kConstParm|kReferenceParm)))
                    throw HandlerError("unsupported_signature","Скалярный DTO поддержан только как возврат по значению");
                const auto scalar_struct=capture_scalar_struct(field,size,array_dim);
                if(offset%scalar_struct.layout.min_alignment)
                    throw HandlerError("unsupported_scalar_struct_layout","Смещение DTO не соответствует отражённому выравниванию");
                captured["aux"]={{"scalar_struct",scalar_struct_snapshot_json(scalar_struct)}};
                scalar_struct_public=scalar_struct_snapshot_json(scalar_struct,false);supported=true;
            }
        }
        else if(type=="StrProperty") {
            supported=size==16;
            if(offset%8)throw HandlerError("unsupported_signature","FString требует выравнивание параметра 8 байт");
            if(!returned && vector_parameter_direction::classify(flags)!=vector_parameter_direction::Direction::input)
                throw HandlerError("unsupported_signature","FString требует const reference input");
            if(!returned && !(flags&kConstParm))
                throw HandlerError("unsupported_signature","FString вход по значению пока не поддержан");
        }
        else if(type=="ArrayProperty") {
            if(returned || !(flags&kConstParm) || vector_parameter_direction::classify(flags)!=vector_parameter_direction::Direction::input)
                throw HandlerError("unsupported_signature","Object array поддержан только как const reference input");
            const auto array=capture_object_array_parameter(field,size,array_dim,flags);
            captured["aux"]={{"object_array",array}};array_public=array.at("public");supported=true;
        }
        else if(type=="Int64Property" || type=="UInt64Property")supported=size==8;
        else if(type=="UInt32Property")supported=size==4;
        else if(type=="ClassProperty" || type=="ObjectProperty") {
            const auto declared=read<uintptr_t>(field+0x70);
            validate_class(reinterpret_cast<void*>(declared));
            captured["aux"]={{"declared",identity(validate_object(reinterpret_cast<void*>(declared)))},
                {"declared_type",{{"name",object_name(reinterpret_cast<void*>(declared))},
                                  {"path",object_path(reinterpret_cast<void*>(declared))}}}};
            if(type=="ClassProperty") {
                const auto meta=read<uintptr_t>(field+0x78);validate_class(reinterpret_cast<void*>(meta));
                captured["aux"]["meta"]=identity(validate_object(reinterpret_cast<void*>(meta)));
                captured["aux"]["meta_type"]={{"name",object_name(reinterpret_cast<void*>(meta))},
                                              {"path",object_path(reinterpret_cast<void*>(meta))}};
            }
            supported=size==8;
        } else if(type=="EnumProperty" || type=="ByteProperty") {
            const auto layout=enum_layout(type,size,field);
            captured["aux"]={{"underlying_type",layout.underlying_type},{"size",layout.size}};
            if(type=="EnumProperty") {
                const auto underlying=read<uintptr_t>(field+0x70);
                const auto underlying_class=read<uintptr_t>(underlying+8);
                captured["aux"]["underlying"]={{"pointer",underlying},{"field_class",underlying_class},
                    {"type_name",read<uint64_t>(underlying_class)},{"owner",read<uintptr_t>(underlying+0x10)},
                    {"next",read<uintptr_t>(underlying+0x18)},{"name",read<uint64_t>(underlying+0x20)},
                    {"array_dim",read<int32_t>(underlying+0x30)},{"size",read<int32_t>(underlying+0x34)},
                    {"flags",read<uint64_t>(underlying+0x38)},{"offset",read<int32_t>(underlying+0x44)}};
            }
            if(layout.object) {
                const auto table=enum_table(layout.object);json entries=json::array();
                for(const auto& entry:table.entries)
                    entries.push_back({{"name",entry.name},{"label",name(entry.name)},{"value",entry.value}});
                const auto enum_address=reinterpret_cast<uintptr_t>(layout.object);
                captured["aux"]["enum"]={{"identity",identity(table.identity)},{"path",table.path},{"entries",std::move(entries)},
                    {"data",read<uintptr_t>(enum_address+0x40)},{"count",read<int32_t>(enum_address+0x48)},
                    {"capacity",read<int32_t>(enum_address+0x4c)}};
            } else captured["aux"]["enum"]=nullptr;
            supported=true;
        }
        if(!supported)throw HandlerError("unsupported_signature","Тип или размер параметра пока не поддержан: "+type);
        json public_parameter={{"name",name(field_name)},{"type",type},{"offset",offset},{"element_size",size},
            {"array_dim",array_dim},{"flags",flags},{"return",returned},{"out",(flags&0x100)!=0},
            {"reference",(flags&kReferenceParm)!=0},{"const",(flags&kConstParm)!=0}};
        if(captured.contains("aux"))public_parameter.update(public_parameter_type_metadata(type,captured.at("aux")));
        if(!vector_public.is_null()) {
            public_parameter["struct"]=vector_public;public_parameter["direction"]=vector_direction;
            if(vector_direction=="input")public_parameter["passing"]=(flags&kConstParm)?"const_reference":"value";
            if(vector_direction=="output")public_parameter["argument_marker"]={{"out",true}};
        }
        if(!scalar_public.is_null()) {
            public_parameter["direction"]="output";public_parameter["argument_marker"]={{"out",true}};
            public_parameter["out_metadata"]=scalar_public;
        }
        if(!transform_public.is_null()) {
            public_parameter["struct"]=transform_public;public_parameter["direction"]=returned?"return":"input";
            public_parameter["passing"]=!returned && (flags&kConstParm)?"const_reference":"value";
            if(!returned)public_parameter["argument_constraints"]={{"rotation_normalization","required"},{"max_norm_squared_deviation",1e-6},{"normalization","none"}};
        }
        if(!rotator_public.is_null()) {
            public_parameter["struct"]=rotator_public;public_parameter["direction"]=returned?"return":"input";
            public_parameter["passing"]=!returned && (flags&kConstParm)?"const_reference":"value";
        }
        if(type=="StrProperty" && !returned) {
            public_parameter["direction"]="input";public_parameter["passing"]="const_reference";
            public_parameter["argument_constraints"]={{"max_utf16_units",string_arguments::max_utf16_units},{"embedded_nul","rejected"},{"ownership","engine_owned_copy"}};
        }
        if(!array_public.is_null()) {
            public_parameter["array"]=array_public;public_parameter["direction"]="input";public_parameter["passing"]="const_reference";
            public_parameter["argument_constraints"]={{"max_elements",std::numeric_limits<int32_t>::max()},{"element_form","null_or_handle"},{"ownership","engine_owned_pointer_copy"}};
        }
        if(!color_public.is_null()) {
            public_parameter["struct"]=color_public;public_parameter["direction"]=returned?"return":"input";public_parameter["passing"]="value";
        }
        if(!scalar_struct_public.is_null()) {
            public_parameter["struct"]=scalar_struct_public;public_parameter["direction"]="return";public_parameter["passing"]="value";
        }
        parameters.push_back(std::move(public_parameter));
        return captured;
    });
    if(count!=declared_count)throw HandlerError("unsupported_signature","NumParms не совпадает с полной цепочкой CPF_Parm");
    json result={{"session",handle_session_},{"target_handle",target_handle},{"function_name",function_name},
        {"target",identity(object_identity)},{"owner",identity(owner_identity)},{"function",identity(function_identity)},
        {"function_outer",read<uintptr_t>(function+0x20)},{"function_flags",function_flags},
        {"header",{{"parameter_count",declared_count},{"parameter_size",parameter_bytes},{"return_offset",return_offset},
                   {"child_head",field_head},{"computed_extent",extent}}},{"fields",fields},
        {"public_function",{{"name",function_name},{"owner",object_name(reinterpret_cast<void*>(owner))},
            {"flags",function_flags},{"parameter_count",declared_count},{"parameter_size",parameter_bytes},
            {"return_offset",return_offset},{"parameters",parameters},{"parameters_truncated",false}}}};
    if(!same_serial_identity(serial_identity(object_identity),serial_identity(validate_object(object)))
       || !same_serial_identity(serial_identity(owner_identity),serial_identity(validate_object(reinterpret_cast<void*>(owner))))
       || !same_serial_identity(serial_identity(function_identity),serial_identity(validate_object(reinterpret_cast<void*>(function)))))
        throw HandlerError("signature_changed","Identity UFunction или её владельца изменилась при чтении");
    if(read<uintptr_t>(function+0x50)!=field_head || read<uint32_t>(function+0xb0)!=function_flags
       || read<uint8_t>(function+0xb4)!=declared_count || read<uint16_t>(function+0xb6)!=parameter_bytes
       || read<uint16_t>(function+0xb8)!=return_offset || read<uintptr_t>(function+0x20)!=result.at("function_outer").get<uintptr_t>())
        throw HandlerError("signature_changed","Заголовок UFunction изменился при чтении");
    for(const auto& captured:fields) {
        const auto field=captured.at("pointer").get<uintptr_t>(),field_class=captured.at("field_class").get<uintptr_t>();
        if(read<uintptr_t>(field+0x18)!=captured.at("next").get<uintptr_t>()
           || read<uintptr_t>(field+0x10)!=captured.at("owner").get<uintptr_t>()
           || read<uintptr_t>(field+8)!=field_class || read<uint64_t>(field_class)!=captured.at("type_name").get<uint64_t>()
           || read<uint64_t>(field+0x20)!=captured.at("name").get<uint64_t>()
           || read<uint64_t>(field+0x38)!=captured.at("flags").get<uint64_t>()
           || read<int32_t>(field+0x44)!=captured.at("offset").get<int32_t>()
           || read<int32_t>(field+0x34)!=captured.at("size").get<int32_t>()
           || read<int32_t>(field+0x30)!=captured.at("array_dim").get<int32_t>())
            throw HandlerError("signature_changed","Цепочка полей UFunction изменилась при чтении");
        if(!captured.contains("aux"))continue;
        const auto& auxiliary=captured.at("aux");const auto type=captured.at("type").get<std::string>();
        if(type=="StructProperty") {
            if(auxiliary.contains("rotator")) {
                const auto current=capture_scalar_struct(field,captured.at("size").get<int>(),captured.at("array_dim").get<int>());
                rotator_arguments::validate(current);
                if(scalar_struct_snapshot_json(current)!=auxiliary.at("rotator"))
                    throw HandlerError("signature_changed","Вложенная reflection FRotator изменилась при чтении сигнатуры");
                continue;
            }
            if(auxiliary.contains("scalar_struct")) {
                const auto current=capture_scalar_struct(field,captured.at("size").get<int>(),captured.at("array_dim").get<int>());
                if(scalar_struct_snapshot_json(current)!=auxiliary.at("scalar_struct"))
                    throw HandlerError("signature_changed","Скалярный DTO изменился при чтении сигнатуры");
            } else if(auxiliary.contains("linear_color")) {
                const auto current=capture_linear_color_parameter(field,captured.at("size").get<int>(),captured.at("array_dim").get<int>());
                if(linear_color_snapshot_json(current)!=auxiliary.at("linear_color"))
                    throw HandlerError("signature_changed","FLinearColor metadata изменились при чтении сигнатуры");
            } else if(auxiliary.contains("transform")) {
                const auto current=capture_transform_parameter(field,captured.at("size").get<int>(),captured.at("array_dim").get<int>());
                if(transform_snapshot_json(current)!=auxiliary.at("transform"))
                    throw HandlerError("signature_changed","Transform layout изменился при чтении сигнатуры");
            } else {
            const auto current=capture_vector_parameter(field,captured.at("size").get<int>(),captured.at("array_dim").get<int>());
            if(vector_snapshot_json(current)!=auxiliary.at("vector"))
                throw HandlerError("signature_changed","FVector layout изменился при чтении сигнатуры");
            }
        } else if(type=="ArrayProperty") {
            const auto current=capture_object_array_parameter(field,captured.at("size").get<int>(),captured.at("array_dim").get<int>(),captured.at("flags").get<uint64_t>());
            if(current!=auxiliary.at("object_array"))throw HandlerError("signature_changed","Object array metadata изменились при чтении сигнатуры");
        } else if(type=="BoolProperty") {
            if(read<uint8_t>(field+0x70)!=auxiliary.at("field_size").get<uint8_t>()
               || read<uint8_t>(field+0x71)!=auxiliary.at("byte_offset").get<uint8_t>()
               || read<uint8_t>(field+0x72)!=auxiliary.at("byte_mask").get<uint8_t>()
               || read<uint8_t>(field+0x73)!=auxiliary.at("field_mask").get<uint8_t>())
                throw HandlerError("signature_changed","Маска BoolProperty изменилась при чтении сигнатуры");
        } else if(type=="ObjectProperty" || type=="ClassProperty") {
            const auto declared=read<uintptr_t>(field+0x70);
            if(identity(validate_object(reinterpret_cast<void*>(declared)))!=auxiliary.at("declared"))
                throw HandlerError("signature_changed","Объявленный класс аргумента изменился при чтении сигнатуры");
            if(json{{"name",object_name(reinterpret_cast<void*>(declared))},
                    {"path",object_path(reinterpret_cast<void*>(declared))}}!=auxiliary.at("declared_type"))
                throw HandlerError("signature_changed","Имя или путь объявленного класса изменились");
            if(type=="ClassProperty") {
                const auto meta=read<uintptr_t>(field+0x78);
                if(identity(validate_object(reinterpret_cast<void*>(meta)))!=auxiliary.at("meta"))
                    throw HandlerError("signature_changed","Метакласс аргумента изменился при чтении сигнатуры");
                if(json{{"name",object_name(reinterpret_cast<void*>(meta))},
                        {"path",object_path(reinterpret_cast<void*>(meta))}}!=auxiliary.at("meta_type"))
                    throw HandlerError("signature_changed","Имя или путь метакласса изменились");
            }
        } else if(type=="EnumProperty" || type=="ByteProperty") {
            const auto declared=enum_layout(type,captured.at("size").get<int>(),field);
            if(declared.underlying_type!=auxiliary.at("underlying_type").get<std::string>() || declared.size!=auxiliary.at("size").get<int>())
                throw HandlerError("signature_changed","Раскладка enum изменилась при чтении сигнатуры");
            if(type=="EnumProperty") {
                const auto underlying=read<uintptr_t>(field+0x70);const auto& before=auxiliary.at("underlying");
                const auto underlying_class=read<uintptr_t>(underlying+8);
                const json current={{"pointer",underlying},{"field_class",underlying_class},
                    {"type_name",read<uint64_t>(underlying_class)},{"owner",read<uintptr_t>(underlying+0x10)},
                    {"next",read<uintptr_t>(underlying+0x18)},{"name",read<uint64_t>(underlying+0x20)},
                    {"array_dim",read<int32_t>(underlying+0x30)},{"size",read<int32_t>(underlying+0x34)},
                    {"flags",read<uint64_t>(underlying+0x38)},{"offset",read<int32_t>(underlying+0x44)}};
                if(current!=before)throw HandlerError("signature_changed","UnderlyingProp изменился при чтении сигнатуры");
            }
            if(declared.object) {
                const auto table=enum_table(declared.object);const auto enum_address=reinterpret_cast<uintptr_t>(declared.object);
                json entries=json::array();
                for(const auto& entry:table.entries)
                    entries.push_back({{"name",entry.name},{"label",name(entry.name)},{"value",entry.value}});
                const json current={{"identity",identity(table.identity)},{"path",table.path},{"entries",std::move(entries)},
                    {"data",read<uintptr_t>(enum_address+0x40)},{"count",read<int32_t>(enum_address+0x48)},
                    {"capacity",read<int32_t>(enum_address+0x4c)}};
                if(current!=auxiliary.at("enum"))throw HandlerError("signature_changed","UEnum изменился при чтении сигнатуры");
            } else if(!auxiliary.at("enum").is_null())throw HandlerError("signature_changed","UEnum исчез при чтении сигнатуры");
        }
    }
    if(resolve(target_handle)!=object)throw HandlerError("signature_changed","Ссылка объекта изменилась при чтении сигнатуры");
    return result;
}
json GameAccess::functions(void* object,int limit,const std::string& query,const std::string& target_handle) {
    json items=json::array();auto klass=read<uintptr_t>(reinterpret_cast<uintptr_t>(object)+0x10);
    bool truncated=false,scan_truncated=false;std::set<uintptr_t> visited_classes;
    for(int depth=0;klass;klass=read<uintptr_t>(klass+0x40),++depth) {
        if(depth>=64 || !visited_classes.insert(klass).second){scan_truncated=true;break;}
        auto field=read<uintptr_t>(klass+0x48);std::set<uintptr_t> visited_fields;
        for(;field;field=read<uintptr_t>(field+0x28)) {
            if(visited_fields.size()>=4096 || !visited_fields.insert(field).second){scan_truncated=true;break;}
            const auto fieldclass=read<uintptr_t>(field+0x10);
            if(!contains(object_name(reinterpret_cast<void*>(fieldclass)),"Function"))continue;
            const auto function_name=object_name(reinterpret_cast<void*>(field));
            if(!function_query::name_matches(function_name,query))continue;
            if(items.size()>=static_cast<size_t>(limit)){truncated=true;break;}
            json parameters=json::array();auto parameter=read<uintptr_t>(field+0x50);
            std::set<uintptr_t> visited_parameters;bool parameters_truncated=false;
            for(;parameter;parameter=read<uintptr_t>(parameter+0x18)) {
                if(!visited_parameters.insert(parameter).second){parameters_truncated=true;break;}
                const auto pf=read<uint64_t>(parameter+0x38);if(!(pf&0x80))continue;
                const auto parameter_type=field_type(parameter,[this](auto f){return name(f);});
                parameters.push_back({{"name",name(read<uint64_t>(parameter+0x20))},
                    {"type",parameter_type},
                    {"offset",read<int32_t>(parameter+0x44)},{"element_size",read<int32_t>(parameter+0x34)},
                    {"array_dim",read<int32_t>(parameter+0x30)},
                    {"flags",pf},{"return",(pf&0x400)!=0},{"out",(pf&0x100)!=0},
                    {"reference",(pf&kReferenceParm)!=0},{"const",(pf&kConstParm)!=0}});
                if(parameter_type=="BoolProperty")parameters.back()["bool_layout"]={
                    {"field_size",read<uint8_t>(parameter+0x70)},{"byte_offset",read<uint8_t>(parameter+0x71)},
                    {"byte_mask",read<uint8_t>(parameter+0x72)},{"field_mask",read<uint8_t>(parameter+0x73)}};
            }
            json item={{"name",function_name},{"owner",object_name(reinterpret_cast<void*>(klass))},
                {"flags",read<uint32_t>(field+0xb0)},{"parameter_count",read<uint8_t>(field+0xb4)},
                {"parameter_size",read<uint16_t>(field+0xb6)},{"return_offset",read<uint16_t>(field+0xb8)},
                {"parameters",parameters},{"parameters_truncated",parameters_truncated},
                {"signature_token",nullptr},{"signature_status",parameters_truncated?"parameters_truncated":"unsupported_signature"}};
            if(!parameters_truncated)try {
                const auto contract=capture_function_contract(object,klass,field,target_handle);
                require_same_function_contract(contract,capture_function_contract(object,klass,field,target_handle));
                item=contract.at("public_function");
                item["signature_token"]=nullptr;
                item["signature_token"]=store_function_contract(signature_contracts_,next_signature_token_,contract);
                item["signature_status"]="complete";
            }catch(const HandlerError& error){item["signature_status"]=error.code();}
            items.push_back(std::move(item));
            if(parameters_truncated)scan_truncated=true;
        }
        if(truncated || scan_truncated)break;
    }
    return {{"items",items},{"truncated",truncated || scan_truncated},{"scan_truncated",scan_truncated},
            {"scan_complete",!truncated && !scan_truncated}};
}
json GameAccess::invoke(void* object,const std::string& function_name,const json& arguments,
                        const std::string& target_handle,const json* expected_token) {
    const auto invocation_identity=validate_object(object);
    auto capture_scalar_struct=[this](uintptr_t field,int size,int dim) {
        return scalar_struct_values::capture(field,size,dim,
            []<class Value>(uintptr_t address){return read<Value>(address);},
            [this](uint64_t value){return name(value);},
            [this](uintptr_t value){return serial_identity(validate_object(reinterpret_cast<void*>(value)));},
            [this](uintptr_t value){validate_class(reinterpret_cast<void*>(value));},
            [this](uintptr_t value){return object_path(reinterpret_cast<void*>(value));});
    };
    json expected_contract=nullptr;
    if(expected_token)expected_contract=checked_function_contract(signature_contracts_,*expected_token,handle_session_,target_handle,function_name);
    auto klass=read<uintptr_t>(reinterpret_cast<uintptr_t>(object)+0x10);uintptr_t function=0,function_owner=0;
    std::set<uintptr_t> visited_classes;
    for(int depth=0;klass && depth<64 && !function;klass=read<uintptr_t>(klass+0x40),++depth) {
        if(!visited_classes.insert(klass).second)throw HandlerError("unsupported_signature","Цепочка владельцев функций циклическая");
        auto field=read<uintptr_t>(klass+0x48);
        std::set<uintptr_t> visited_functions;
        for(int count=0;field && count<4096;field=read<uintptr_t>(field+0x28),++count) {
            if(!visited_functions.insert(field).second)throw HandlerError("unsupported_signature","Цепочка UFunction циклическая");
            if(object_name(reinterpret_cast<void*>(field))==function_name && contains(object_name(reinterpret_cast<void*>(read<uintptr_t>(field+0x10))),"Function")){function=field;function_owner=klass;break;}
        }
    }
    if(!function)throw HandlerError("function_not_found","Отражённая функция не найдена");
    if(expected_token) {
        if(expected_contract.at("function").at("object")!=function || expected_contract.at("owner").at("object")!=function_owner)
            throw HandlerError("signature_token_mismatch","Токен выдан другой реализации одноимённой функции");
        require_same_function_contract(expected_contract,capture_function_contract(object,function_owner,function,target_handle));
    }
    auto bytes=read<uint16_t>(function+0xb6);
    std::vector<uintptr_t> parameter_fields;
    std::set<uintptr_t> visited_fields;
    auto parameter=read<uintptr_t>(function+0x50);
    int extent=bytes;
    for(;parameter;parameter=read<uintptr_t>(parameter+0x18)) {
        if(!visited_fields.insert(parameter).second)
            throw HandlerError("unsupported_signature","Цепочка отражённых параметров содержит цикл");
        const auto flags=read<uint64_t>(parameter+0x38);
        if(!(flags&0x80))continue;
        extent=parameter_extent(extent,read<int32_t>(parameter+0x44),
            read<int32_t>(parameter+0x34),read<int32_t>(parameter+0x30));
        parameter_fields.push_back(parameter);
    }
    aligned_parameter_buffer::Buffer aligned(static_cast<size_t>(extent));auto buffer=aligned.data();
    uintptr_t return_field{};std::string return_type;int return_size{};int return_offset{};size_t input_count{};
    std::vector<std::pair<std::string,void*>> object_arguments;
    struct ClassArgument {std::string handle;void* property_class;void* meta_class;};
    std::vector<ClassArgument> class_arguments;
    struct EnumArgument {uintptr_t field;std::string type;EnumLayout layout;
                         g1r::enum_values::Integer value;std::optional<Handle> enum_identity;};
    std::vector<EnumArgument> enum_inputs;
    struct NameSource {
        NameArgumentSource source;
        ObjectSerialIdentity object_identity,owner_identity;
        uintptr_t field{},field_next{},field_class{},owner{};
        uint64_t field_name{},packed{};
        int32_t offset{},size{},dim{},owner_size{};
        std::string text;
        bool operator==(const NameSource&) const=default;
    };
    auto capture_name=[this](const NameArgumentSource& source) {
        NameSource result;result.source=source;
        auto object=resolve(source.handle);
        result.object_identity=serial_identity(validate_object(object));
        auto klass=result.object_identity.object_class;
        std::set<uintptr_t> classes,fields;int scanned=0;
        for(int depth=0;klass && !result.field;klass=read<uintptr_t>(klass+0x40),++depth) {
            if(depth>=64 || !classes.insert(klass).second)
                throw HandlerError("name_source_scan_incomplete","Цепочка классов источника FName не завершена");
            validate_class(reinterpret_cast<void*>(klass));
            for(auto field=read<uintptr_t>(klass+0x50);field;field=read<uintptr_t>(field+0x18)) {
                if(++scanned>4096 || !fields.insert(field).second)
                    throw HandlerError("name_source_scan_incomplete","Список свойств источника FName не завершён");
                if(name(read<uint64_t>(field+0x20))==source.property_name){result.field=field;result.owner=klass;break;}
            }
        }
        if(!result.field)throw HandlerError("property_not_found","Свойство источника FName не найдено");
        result.owner_identity=serial_identity(validate_object(reinterpret_cast<void*>(result.owner)));
        result.field_next=read<uintptr_t>(result.field+0x18);result.field_class=read<uintptr_t>(result.field+8);
        result.field_name=read<uint64_t>(result.field+0x20);
        result.offset=read<int32_t>(result.field+0x44);result.size=read<int32_t>(result.field+0x34);
        result.dim=read<int32_t>(result.field+0x30);result.owner_size=read<int32_t>(result.owner+0x58);
        if(field_type(result.field,[this](uint64_t value){return name(value);})!="NameProperty"
           || result.size!=8 || result.dim!=1 || result.offset<0
           || result.owner_size<8 || result.offset>result.owner_size-8)
            throw HandlerError("unsupported_name_source","Источник должен быть точным NameProperty размером 8 байт");
        const auto address=reinterpret_cast<uintptr_t>(object)+static_cast<uintptr_t>(result.offset);
        if(address<reinterpret_cast<uintptr_t>(object) || address>UINTPTR_MAX-sizeof(uint64_t))
            throw HandlerError("unsupported_name_source","Адрес источника FName переполняется");
        result.packed=read<uint64_t>(address);
        result.text=name(result.packed);
        if(result.packed!=read<uint64_t>(address)
           || result.field_next!=read<uintptr_t>(result.field+0x18)
           || result.field_class!=read<uintptr_t>(result.field+8)
           || result.field_name!=read<uint64_t>(result.field+0x20)
           || result.offset!=read<int32_t>(result.field+0x44) || result.size!=read<int32_t>(result.field+0x34)
           || result.dim!=read<int32_t>(result.field+0x30) || result.owner_size!=read<int32_t>(result.owner+0x58)
           || field_type(result.field,[this](uint64_t value){return name(value);})!="NameProperty"
           || !same_serial_identity(result.owner_identity,serial_identity(validate_object(reinterpret_cast<void*>(result.owner))))
           || !same_serial_identity(result.object_identity,serial_identity(validate_object(object))))
            throw HandlerError("name_source_changed","Метаданные или identity источника FName изменились при копировании");
        if(resolve(source.handle)!=object)throw HandlerError("stale_handle","Ссылка источника FName изменилась");
        return result;
    };
    struct NameInput {uintptr_t field{};int32_t offset{};NameSource source;};
    std::vector<NameInput> name_inputs;
    struct VectorParameter {uintptr_t field{};int offset{},size{};uint64_t flags{};std::string key;vector_layout::Snapshot layout;bool output{},returned{};};
    std::vector<VectorParameter> vector_parameters;
    struct ColorParameter {uintptr_t field{};int offset{};uint64_t flags{};linear_color_layout::Snapshot layout;bool returned{};};
    std::vector<ColorParameter> color_parameters;
    struct ScalarOutput {uintptr_t field{};int offset{};uint64_t flags{};std::string key;scalar_outputs::Layout layout;};
    std::vector<ScalarOutput> scalar_parameters;
    struct TransformReturn {uintptr_t field{};int offset{};transform_layout::Snapshot layout;};
    std::optional<TransformReturn> transform_return;
    std::optional<scalar_struct_values::Snapshot> scalar_struct_return;
    json pod_function_contract=nullptr;
    bool contains_vector=false,contains_transform=false,contains_color=false,contains_rotator=false,contains_scalar_struct=false,contains_scalar_output=false,contains_string_input=false,contains_object_array=false;
    bool has_string_return=false;
    for(const auto field:parameter_fields) {
        const auto type=field_type(field,[this](auto f){return name(f);});const auto flags=read<uint64_t>(field+0x38);
        if(type=="StructProperty") {
            const auto structure_path=object_path(reinterpret_cast<void*>(read<uintptr_t>(field+0x70)));
            if(structure_path=="/Script/CoreUObject.LinearColor")contains_color=true;
            else if(structure_path=="/Script/CoreUObject.Transform")contains_transform=true;
            else if(structure_path=="/Script/CoreUObject.Vector")contains_vector=true;
            else if(structure_path=="/Script/CoreUObject.Rotator")contains_rotator=true;
            else contains_scalar_struct=true;
        }
        if(supported_scalar_output_type(type) && (flags&0x100) && !(flags&0x400))contains_scalar_output=true;
        if(type=="StrProperty" && !(flags&0x400))contains_string_input=true;
        if(type=="StrProperty" && (flags&0x400))has_string_return=true;
        if(type=="ArrayProperty")contains_object_array=true;
    }
    if(contains_object_array && (contains_string_input || has_string_return))
        throw HandlerError("unsupported_signature","Object array входы пока не смешиваются с FString входом или возвратом");
    const bool strict_pod_contract=contains_vector || contains_transform || contains_color || contains_rotator || contains_scalar_struct || contains_scalar_output || contains_string_input || contains_object_array;
    if(strict_pod_contract) {
        pod_function_contract=capture_function_contract(object,function_owner,function,target_handle);
        std::vector<std::pair<int,int>> intervals;
        for(const auto field:parameter_fields) {
            const auto offset=read<int32_t>(field+0x44),size=read<int32_t>(field+0x34);
            if(parameter_extent(0,offset,size,read<int32_t>(field+0x30))>extent)
                throw HandlerError("unsupported_signature","Параметр выходит за выделенный буфер UFunction");
            for(const auto& [start,end]:intervals)if(offset<end && start<offset+size)
                throw HandlerError("unsupported_signature","POD функция содержит перекрывающиеся параметры");
            intervals.emplace_back(offset,offset+size);
        }
    }
    struct ArrayElement {std::string handle;ObjectSerialIdentity identity{};uintptr_t pointer{};};
    struct ObjectArrayInput {uintptr_t field{};int offset{};uint64_t flags{};std::string key;json metadata;std::vector<ArrayElement> elements;ArrayHeader header{};std::vector<uintptr_t> pointers;};
    std::vector<ObjectArrayInput> array_inputs;
    object_array_input_lifetime::Ledger array_ledger;
    bool array_cleanup_done=false;
    CleanupOnce<object_array_input_lifetime::Cleanup> array_cleanup_once;
    std::size_t extra_array_count{},extra_array_bytes{};
    uint32_t array_quarantine_cause{};
    auto quarantine_arrays=[&](uint32_t cause) {
        array_ledger.mark_uncertain();object_array_inputs_quarantine_cause_=cause;
        object_array_inputs_retained_count_=array_ledger.retained_count()+extra_array_count;
        object_array_inputs_retained_bytes_=array_ledger.retained_bytes()+extra_array_bytes;
        object_array_inputs_quarantined_=true;
    };
    auto release_arrays=[&] {
        return array_cleanup_once.run([&]() noexcept -> object_array_input_lifetime::Cleanup {
            const auto cleanup=array_ledger.release([this](uintptr_t memory){return free_string(base_+kFree,reinterpret_cast<void*>(memory));});
            array_cleanup_done=cleanup.confirmed;
            if(!cleanup.confirmed)quarantine_arrays(array_quarantine_cause?array_quarantine_cause:5);
            return cleanup;
        });
    };
    if(contains_object_array) {
        if(object_array_inputs_quarantined_)throw HandlerError("object_array_input_quarantined","Object array входы заблокированы после неподтверждённой очистки");
        uint8_t realloc_bytes[sizeof(kObjectArrayReallocBytes)]{},free_bytes[sizeof(kStringFreeBytes)]{};
        if(!copy_memory(realloc_bytes,reinterpret_cast<void*>(base_+kObjectArrayRealloc),sizeof(realloc_bytes))
           || !copy_memory(free_bytes,reinterpret_cast<void*>(base_+kFree),sizeof(free_bytes))
           || std::memcmp(realloc_bytes,kObjectArrayReallocBytes,sizeof(realloc_bytes))
           || std::memcmp(free_bytes,kStringFreeBytes,sizeof(free_bytes)))
            throw HandlerError("unsupported_array_allocator","Подписи Realloc или Free объектного массива не совпадают");
        object_array_input_lifetime::InputBudget budget;
        array_inputs.reserve(parameter_fields.size());
        for(const auto field:parameter_fields) {
            const auto flags=read<uint64_t>(field+0x38);
            if(field_type(field,[this](auto value){return name(value);})!="ArrayProperty")continue;
            const auto metadata=capture_object_array_parameter(field,read<int32_t>(field+0x34),read<int32_t>(field+0x30),flags);
            const auto key=name(read<uint64_t>(field+0x20));
            if(!arguments.contains(key))throw HandlerError("missing_argument","Не задан Object array аргумент "+key);
            const auto& values=arguments.at(key);
            if(!values.is_array())
                throw HandlerError("invalid_argument","Object array требует JSON-массив");
            budget.add(values.size());
            ObjectArrayInput input{field,read<int32_t>(field+0x44),flags,key,metadata,{},{},{}};
            input.elements.reserve(values.size());
            input.pointers.resize(values.size());
            const auto declared=reinterpret_cast<void*>(metadata.at("property_class").get<uintptr_t>());
            for(const auto& value:values) {
                auto handle=object_argument_handle(value);ArrayElement element{handle,{},0};
                if(!handle.empty()) {
                    auto object_value=resolve(handle);
                    if(!is_compatible_object(object_value,declared))
                        throw HandlerError("incompatible_object_argument","Элемент массива не соответствует PropertyClass");
                    element.identity=serial_identity(validate_object(object_value));element.pointer=reinterpret_cast<uintptr_t>(object_value);
                }
                input.elements.push_back(std::move(element));
            }
            array_inputs.push_back(std::move(input));
        }
        array_ledger.reserve_inputs(array_inputs.size());
        require_same_function_contract(pod_function_contract,capture_function_contract(object,function_owner,function,target_handle));
    }
    auto validate_array_elements=[&] {
        for(const auto& input:array_inputs) {
            const auto declared=reinterpret_cast<void*>(input.metadata.at("property_class").get<uintptr_t>());
            for(const auto& element:input.elements)if(!element.handle.empty()) {
                auto current=resolve(element.handle);
                if(reinterpret_cast<uintptr_t>(current)!=element.pointer
                   || !same_serial_identity(element.identity,serial_identity(validate_object(current)))
                   || !is_compatible_object(current,declared))
                    throw HandlerError("stale_handle","Identity элемента Object array изменилась до вызова");
            }
        }
    };
    auto validate_array_inputs=[&] {
        if(!contains_object_array)return;
        for(size_t index=0;index<array_inputs.size();++index) {
            auto& input=array_inputs[index];ArrayHeader current{};
            if(!copy_memory(&current,buffer+input.offset,sizeof(current))) {
                array_ledger.mark_uncertain();array_quarantine_cause=3;
                throw HandlerError("array_lifetime_unknown","Заголовок Object array недоступен");
            }
            array_ledger.validate_unchanged_input(index,current);
            if(current.count && !copy_memory(input.pointers.data(),reinterpret_cast<void*>(current.data),input.elements.size()*sizeof(uintptr_t))) {
                array_ledger.mark_uncertain();array_quarantine_cause=3;
                throw HandlerError("array_lifetime_unknown","Байты Object array недоступны");
            }
            for(size_t item=0;item<input.elements.size();++item)if(input.pointers[item]!=input.elements[item].pointer) {
                array_ledger.mark_uncertain();array_quarantine_cause=3;
                throw HandlerError("array_lifetime_unknown","Указатели const Object array изменились");
            }
        }
        require_same_function_contract(pod_function_contract,capture_function_contract(object,function_owner,function,target_handle));
    };
    struct StringInput {uintptr_t field{};int offset{};uint64_t flags{};std::string key;std::u16string source;StringHeader header{};};
    std::vector<StringInput> string_inputs;
    string_input_lifetime::Ledger string_ledger;
    as_exception_capture::State script_errors;
    bool string_event_started=false,string_event_completed=false,string_return_registered=false,string_cleanup_done=false;
    CleanupOnce<string_input_lifetime::Cleanup> string_cleanup_once;
    std::size_t extra_retained_count{},extra_retained_bytes{};
    uint32_t string_quarantine_cause{};
    auto quarantine_strings=[&](uint32_t cause) {
        string_ledger.mark_uncertain();
        string_inputs_quarantine_cause_=cause;
        string_inputs_retained_count_=string_ledger.retained_count()+extra_retained_count;
        string_inputs_retained_bytes_=string_ledger.retained_bytes()+extra_retained_bytes;
        string_inputs_quarantined_=true;
    };
    auto release_strings=[&] {
        return string_cleanup_once.run([&]() noexcept -> string_input_lifetime::Cleanup {
            const auto cleanup=string_ledger.release([this](uintptr_t memory){return free_string(base_+kFree,reinterpret_cast<void*>(memory));});
            string_cleanup_done=cleanup.confirmed;
            if(!cleanup.confirmed)quarantine_strings(string_quarantine_cause?string_quarantine_cause:5);
            return cleanup;
        });
    };
    if(contains_string_input) {
        if(string_inputs_quarantined_)throw HandlerError("string_input_quarantined","FString входы заблокированы после неподтверждённой очистки");
        uint8_t constructor_bytes[sizeof(kStringConstructBytes)]{},free_bytes[sizeof(kStringFreeBytes)]{};
        if(!copy_memory(constructor_bytes,reinterpret_cast<void*>(base_+kStringConstruct),sizeof(constructor_bytes))
           || !copy_memory(free_bytes,reinterpret_cast<void*>(base_+kFree),sizeof(free_bytes))
           || std::memcmp(constructor_bytes,kStringConstructBytes,sizeof(constructor_bytes))
           || std::memcmp(free_bytes,kStringFreeBytes,sizeof(free_bytes)))
            throw HandlerError("unsupported_string_allocator","Подписи конструктора или освобождения FString не совпадают");
        string_input_lifetime::InputBudget budget;
        string_inputs.reserve(parameter_fields.size());
        for(const auto field:parameter_fields) {
            const auto flags=read<uint64_t>(field+0x38);
            if((flags&0x400) || field_type(field,[this](auto value){return name(value);})!="StrProperty")continue;
            const auto offset=read<int32_t>(field+0x44),size=read<int32_t>(field+0x34),dim=read<int32_t>(field+0x30);
            if(size!=16 || dim!=1 || offset%8 || !(flags&kConstParm)
               || vector_parameter_direction::classify(flags)!=vector_parameter_direction::Direction::input)
                throw HandlerError("unsupported_signature","FString требует выровненный const reference input размером 16 байт");
            const auto key=name(read<uint64_t>(field+0x20));
            if(!arguments.contains(key))throw HandlerError("missing_argument","Не задан FString аргумент "+key);
            auto source=string_arguments::encode(arguments.at(key));budget.add(source.size());
            string_inputs.push_back({field,offset,flags,key,std::move(source),{}});
        }
        string_ledger.prepare(string_inputs.size());
        require_same_function_contract(pod_function_contract,capture_function_contract(object,function_owner,function,target_handle));
    }
    auto validate_string_inputs=[&] {
        if(!contains_string_input)return;
        for(size_t index=0;index<string_inputs.size();++index) {
            const auto& input=string_inputs[index];StringHeader current{};
            if(!copy_memory(&current,buffer+input.offset,sizeof(current))) {
                string_ledger.mark_uncertain();string_quarantine_cause=3;
                throw HandlerError("string_lifetime_unknown","Заголовок FString входа недоступен");
            }
            string_ledger.validate_unchanged_input(index,current);
            std::u16string copied(input.source.size()+1,u'\0');
            if(current.data && (!copy_memory(copied.data(),reinterpret_cast<void*>(current.data),copied.size()*sizeof(char16_t))
               || copied.back()!=0 || !std::equal(input.source.begin(),input.source.end(),copied.begin()))) {
                string_ledger.mark_uncertain();string_quarantine_cause=3;
                throw HandlerError("string_lifetime_unknown","Байты FString const-входа изменились либо недоступны");
            }
        }
        require_same_function_contract(pod_function_contract,capture_function_contract(object,function_owner,function,target_handle));
    };
    try {
    for(const auto field:parameter_fields) {
        auto flags=read<uint64_t>(field+0x38);if(!(flags&0x80))continue;
        auto type=field_type(field,[this](auto f){return name(f);});int offset=read<int32_t>(field+0x44),size=read<int32_t>(field+0x34);
        const auto current_extent=parameter_extent(extent,offset,size,read<int32_t>(field+0x30));
        if(current_extent>extent)
            throw HandlerError("unsupported_signature","Параметр изменился и выходит за выделенный буфер UFunction");
        if(flags&0x400){
            if(return_field)throw HandlerError("unsupported_signature","У функции обнаружено несколько возвращаемых параметров");
            return_field=field;return_type=type;return_size=size;return_offset=offset;
            if(supported_scalar_output_type(type) && (flags&(kReferenceParm|kConstParm)))
                throw HandlerError("unsupported_signature","Const/ref скалярный возврат не поддержан");
            if(type=="StructProperty") {
                const auto structure_path=object_path(reinterpret_cast<void*>(read<uintptr_t>(field+0x70)));
                if(structure_path=="/Script/CoreUObject.LinearColor") {
                    if(!contains_color)throw HandlerError("signature_changed","FLinearColor возврат появился после чтения полной сигнатуры");
                    if(flags&(kConstParm|kReferenceParm))throw HandlerError("unsupported_signature","Const/ref возврат FLinearColor не поддержан");
                    const auto color=capture_linear_color_parameter(field,size,read<int32_t>(field+0x30));
                    color_parameters.push_back({field,offset,flags,color,true});
                } else if(structure_path=="/Script/CoreUObject.Transform") {
                    if(!contains_transform)throw HandlerError("signature_changed","Transform возврат появился после чтения полной сигнатуры");
                    if(flags&(kConstParm|kReferenceParm))throw HandlerError("unsupported_signature","Const/ref возврат Transform не поддержан");
                    const auto transform=capture_transform_parameter(field,size,read<int32_t>(field+0x30));
                    if(offset%transform.layout.min_alignment
                       || reinterpret_cast<uintptr_t>(buffer+offset)%static_cast<uintptr_t>(transform.layout.min_alignment))
                        throw HandlerError("unsupported_transform_layout","Буфер Transform не соответствует отражённому выравниванию");
                    transform_return=TransformReturn{field,offset,transform};
                } else if(structure_path=="/Script/CoreUObject.Vector") {
                    if(!contains_vector)throw HandlerError("signature_changed","FVector возврат появился после чтения полной сигнатуры");
                    if(flags&(kReferenceParm|kConstParm))
                        throw HandlerError("unsupported_signature","Const/ref возврат FVector не поддержан");
                    const auto layout=capture_vector_parameter(field,size,read<int32_t>(field+0x30));
                    vector_parameters.push_back({field,offset,size,flags,name(read<uint64_t>(field+0x20)),layout,false,true});
                } else if(structure_path=="/Script/CoreUObject.Rotator") {
                    if(!contains_rotator)throw HandlerError("signature_changed","FRotator возврат появился после чтения полной сигнатуры");
                    if(flags&(kConstParm|kReferenceParm))throw HandlerError("unsupported_signature","Const/ref возврат FRotator не поддержан");
                    auto rotator=capture_scalar_struct(field,size,read<int32_t>(field+0x30));
                    rotator_arguments::validate(rotator);
                    if(offset%rotator.layout.min_alignment
                       || reinterpret_cast<uintptr_t>(buffer+offset)%static_cast<uintptr_t>(rotator.layout.min_alignment))
                        throw HandlerError("unsupported_rotator_layout","Буфер FRotator не соответствует отражённому выравниванию");
                    scalar_struct_return=std::move(rotator);
                } else {
                    if(!contains_scalar_struct)throw HandlerError("signature_changed","Скалярный DTO возврат появился после чтения полной сигнатуры");
                    if(flags&(kConstParm|kReferenceParm))throw HandlerError("unsupported_signature","Const/ref возврат DTO не поддержан");
                    auto scalar_struct=capture_scalar_struct(field,size,read<int32_t>(field+0x30));
                    if(offset%scalar_struct.layout.min_alignment
                       || reinterpret_cast<uintptr_t>(buffer+offset)%static_cast<uintptr_t>(scalar_struct.layout.min_alignment))
                        throw HandlerError("unsupported_scalar_struct_layout","Буфер DTO не соответствует отражённому выравниванию");
                    scalar_struct_return=std::move(scalar_struct);
                }
                continue;
            }
            if(type=="NameProperty")validate_name_return_layout(size,read<int32_t>(field+0x30));
            else if(type=="EnumProperty" || type=="ByteProperty")enum_layout(type,size,field);
            else if(type=="ObjectProperty" || type=="ClassProperty") {
                if(size!=8)throw HandlerError("unsupported_signature","Размер объектного возврата не поддержан");
                validate_class(reinterpret_cast<void*>(read<uintptr_t>(field+0x70)));
                if(type=="ClassProperty")validate_class(reinterpret_cast<void*>(read<uintptr_t>(field+0x78)));
            } else if((type=="StrProperty" && size!=16)
               || (type!="StrProperty" && property_value(reinterpret_cast<uintptr_t>(buffer+offset),type,size,field).is_null()))
                throw HandlerError("unsupported_signature","Тип результата пока не поддержан");
            continue;
        }
        if(type=="StructProperty") {
            const auto structure_path=object_path(reinterpret_cast<void*>(read<uintptr_t>(field+0x70)));
            if(structure_path=="/Script/CoreUObject.Rotator") {
                if(!contains_rotator)throw HandlerError("signature_changed","FRotator параметр появился после чтения полной сигнатуры");
                if(vector_parameter_direction::classify(flags)!=vector_parameter_direction::Direction::input)
                    throw HandlerError("unsupported_signature","FRotator требует вход по значению либо const reference; mutable Out не поддержан");
                const auto rotator=capture_scalar_struct(field,size,read<int32_t>(field+0x30));
                rotator_arguments::validate(rotator);
                if(offset%rotator.layout.min_alignment
                   || reinterpret_cast<uintptr_t>(buffer+offset)%static_cast<uintptr_t>(rotator.layout.min_alignment))
                    throw HandlerError("unsupported_rotator_layout","Буфер FRotator не соответствует отражённому выравниванию");
                const auto key=name(read<uint64_t>(field+0x20));const auto found=arguments.find(key);
                if(found==arguments.end())throw HandlerError("invalid_arguments","Не указан аргумент FRotator: "+key);
                if(!found->is_object() || found->size()!=1 || !found->contains("rotator"))
                    throw HandlerError("invalid_argument","FRotator требует только {rotator:{Pitch,Yaw,Roll}}");
                const auto encoded=rotator_arguments::encode(rotator.layout,found->at("rotator"));
                std::memcpy(buffer+offset,encoded.data(),encoded.size());
                ++input_count;continue;
            }
            if(structure_path=="/Script/CoreUObject.LinearColor") {
                if(!contains_color)throw HandlerError("signature_changed","FLinearColor вход появился после чтения полной сигнатуры");
                if(flags&(kConstParm|kReferenceParm|0x100))throw HandlerError("unsupported_signature","FLinearColor вход поддержан только по значению");
                const auto key=name(read<uint64_t>(field+0x20));
                if(!arguments.contains(key))throw HandlerError("missing_argument","Не задан FLinearColor аргумент "+key);
                const auto color=capture_linear_color_parameter(field,size,read<int32_t>(field+0x30));
                const auto encoded=linear_color_arguments::encode(color.layout,arguments.at(key));
                std::memcpy(buffer+offset,encoded.bytes.data(),static_cast<size_t>(encoded.size));
                color_parameters.push_back({field,offset,flags,color,false});++input_count;continue;
            } else if(structure_path=="/Script/CoreUObject.Transform") {
                if(!contains_transform)throw HandlerError("signature_changed","Transform появился после чтения полной сигнатуры");
                if(vector_parameter_direction::classify(flags)!=vector_parameter_direction::Direction::input)
                    throw HandlerError("unsupported_signature","Transform требует вход по значению либо const reference; Out/Return не поддержаны");
                const auto key=name(read<uint64_t>(field+0x20));
                if(!arguments.contains(key))throw HandlerError("missing_argument","Не задан Transform аргумент "+key);
                const auto transform=capture_transform_parameter(field,size,read<int32_t>(field+0x30));
                if(offset%transform.layout.min_alignment
                   || reinterpret_cast<uintptr_t>(buffer+offset)%static_cast<uintptr_t>(transform.layout.min_alignment))
                    throw HandlerError("unsupported_transform_layout","Буфер Transform не соответствует отражённому выравниванию");
                const auto encoded=transform_arguments::encode(transform.layout,arguments.at(key));
                std::memcpy(buffer+offset,encoded.bytes.data(),static_cast<size_t>(encoded.size));
                ++input_count;continue;
            }
            if(structure_path!="/Script/CoreUObject.Vector")
                throw HandlerError("unsupported_signature","Скалярный DTO поддержан только как возврат по значению");
            if(!contains_vector)throw HandlerError("signature_changed","FVector параметр появился после чтения полной сигнатуры");
            const auto direction=vector_parameter_direction::classify(flags);
            const auto key=name(read<uint64_t>(field+0x20));
            if(!arguments.contains(key))throw HandlerError("missing_argument","Не задан FVector аргумент "+key);
            const auto layout=capture_vector_parameter(field,size,read<int32_t>(field+0x30));
            if(direction==vector_parameter_direction::Direction::output) {
                vector_parameter_direction::validate_output_marker(arguments.at(key));
                std::memset(buffer+offset,0,static_cast<size_t>(size));
            } else {
                const auto encoded=vector_arguments::encode(layout.layout,arguments.at(key));
                std::memcpy(buffer+offset,encoded.bytes.data(),static_cast<size_t>(encoded.size));
            }
            ++input_count;
            vector_parameters.push_back({field,offset,size,flags,key,layout,direction==vector_parameter_direction::Direction::output,false});
            continue;
        }
        if(type=="StrProperty") {
            if(!contains_string_input)throw HandlerError("signature_changed","FString вход появился после чтения полной сигнатуры");
            auto input=std::find_if(string_inputs.begin(),string_inputs.end(),[field](const auto& value){return value.field==field;});
            if(input==string_inputs.end() || input->offset!=offset || input->flags!=flags || size!=16 || offset%8)
                throw HandlerError("signature_changed","FString вход изменился после предварительной проверки");
            ++input_count;continue;
        }
        if(type=="ArrayProperty") {
            if(!contains_object_array)throw HandlerError("signature_changed","Object array появился после чтения полной сигнатуры");
            auto input=std::find_if(array_inputs.begin(),array_inputs.end(),[field](const auto& value){return value.field==field;});
            if(input==array_inputs.end() || input->offset!=offset || input->flags!=flags || size!=16 || offset%8)
                throw HandlerError("signature_changed","Object array вход изменился после предварительной проверки");
            ++input_count;continue;
        }
        if(supported_scalar_output_type(type) && (flags&0x100)) {
            if(!contains_scalar_output)throw HandlerError("signature_changed","Скалярный out появился после чтения полной сигнатуры");
            if(parameter_direction::classify(flags)!=parameter_direction::Direction::output)
                throw HandlerError("unsupported_signature","Скалярный параметр не является pure out");
            const auto key=name(read<uint64_t>(field+0x20));
            if(!arguments.contains(key))throw HandlerError("missing_argument","Не задан скалярный out аргумент "+key);
            parameter_direction::validate_output_marker(arguments.at(key));
            const auto layout=capture_scalar_output_layout(field,type,size,read<int32_t>(field+0x30));
            std::memset(buffer+offset,0,static_cast<size_t>(size));
            scalar_parameters.push_back({field,offset,flags,key,layout});
            ++input_count;continue;
        }
        if(flags&(0x100|kReferenceParm))throw HandlerError("unsupported_signature","Out/ref аргументы этого типа не поддержаны");
        if(type=="NameProperty")validate_name_argument_layout(size,read<int32_t>(field+0x30));
        auto key=name(read<uint64_t>(field+0x20));if(!arguments.contains(key))throw HandlerError("missing_argument","Не задан аргумент "+key);auto v=arguments.at(key);++input_count;
        if(type=="NameProperty") {
            if(v.is_object() && v.contains("name")) {
                if(v.size()!=1 || !v.at("name").is_string())
                    throw HandlerError("invalid_name_argument","Текстовый FName требует только {name:строка}");
                const auto& text=v.at("name").get_ref<const std::string&>();
                if(text.find('\0')!=std::string::npos)
                    throw HandlerError("invalid_name_argument","Текстовый FName не принимает встроенный NUL");
                std::wstring encoded;
                if(!text.empty()) {
                    const auto length=static_cast<int>(text.size());
                    const auto units=MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,text.data(),length,nullptr,0);
                    if(!units)throw HandlerError("invalid_name_argument","Текстовый FName содержит неверный UTF-8");
                    encoded.resize(static_cast<size_t>(units));
                    if(MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,text.data(),length,encoded.data(),units)!=units)
                        throw HandlerError("invalid_name_argument","Текстовый FName не удалось преобразовать в UTF-16");
                }
                uint64_t packed{};
                if(!construct_name(base_+kNameConstruct,&packed,reinterpret_cast<const char16_t*>(encoded.c_str())))
                    throw HandlerError("name_constructor_fault","Конструктор FName вызвал native exception");
                std::memcpy(buffer+offset,&packed,sizeof(packed));
            } else {
                const auto source=validated_name_argument(size,read<int32_t>(field+0x30),v,capture_name);
                std::memcpy(buffer+offset,&source.packed,sizeof(source.packed));
                name_inputs.push_back({field,offset,source});
            }
        }
        else if(type=="BoolProperty" && v.is_boolean()){auto bo=read<uint8_t>(field+0x71),mask=read<uint8_t>(field+0x73);validate_bool_layout(size,bo,mask);buffer[offset+bo]=v.get<bool>()?mask:0;}
        else if(type=="IntProperty" && size==4){*reinterpret_cast<int32_t*>(buffer+offset)=int32_argument(v);}
        else if(type=="Int64Property" && size==8){const auto integer=int64_argument(v);std::memcpy(buffer+offset,&integer,sizeof(integer));}
        else if(type=="UInt64Property" && size==8){const auto integer=uint64_argument(v);std::memcpy(buffer+offset,&integer,sizeof(integer));}
        else if(type=="UInt32Property" && size==4){const auto integer=uint32_argument(v);std::memcpy(buffer+offset,&integer,sizeof(integer));}
        else if(type=="FloatProperty" && size==4){*reinterpret_cast<float*>(buffer+offset)=float_argument(v);}
        else if(type=="DoubleProperty" && size==8){*reinterpret_cast<double*>(buffer+offset)=double_argument(v);}
        else if(type=="EnumProperty" || type=="ByteProperty") {
            const auto declared=enum_layout(type,size,field);
            const auto encoded=enum_arguments::encode_integer(declared.underlying_type,size,v);
            std::optional<Handle> identity;
            if(type=="EnumProperty" || declared.object) {
                const auto table=enum_table(declared.object);
                if(!enum_arguments::contains_value(encoded.value,table.entries))
                    throw HandlerError("unknown_enum_value","Число отсутствует среди объявленных значений UEnum");
                identity=table.identity;
            }
            std::memcpy(buffer+offset,encoded.bytes.data(),encoded.size);
            enum_inputs.push_back({field,type,declared,encoded.value,identity});
        }
        else if(type=="ObjectProperty" && size==8) {
            auto expected=reinterpret_cast<void*>(read<uintptr_t>(field+0x70));
            validate_class(expected);
            const auto handle=object_argument_handle(v);
            void* argument=nullptr;
            if(!handle.empty()) {
                argument=resolve(handle);
                if(!is_compatible_object(argument,expected))
                    throw HandlerError("incompatible_object_argument","Ссылка не принадлежит объявленному классу аргумента");
                object_arguments.emplace_back(handle,expected);
            }
            *reinterpret_cast<void**>(buffer+offset)=argument;
        }
        else if(type=="ClassProperty" && size==8) {
            const auto property_class=reinterpret_cast<void*>(read<uintptr_t>(field+0x70));
            const auto meta_class=reinterpret_cast<void*>(read<uintptr_t>(field+0x78));
            validate_class(property_class);validate_class(meta_class);
            const auto handle=class_argument_handle(v);
            void* argument=nullptr;
            if(!handle.empty()) {
                argument=resolve(handle);
                try { validate_class(argument); }
                catch(const HandlerError& error) {
                    if(error.code()!="invalid_object_class")throw;
                    throw HandlerError("invalid_class_argument","Ссылка указывает на UObject, не являющийся UClass");
                }
                if(!is_compatible_class(argument,property_class,meta_class))
                    throw HandlerError("incompatible_class_argument","Представляемый класс не соответствует ограничению аргумента");
                class_arguments.push_back({handle,property_class,meta_class});
            }
            *reinterpret_cast<void**>(buffer+offset)=argument;
        }
        else throw HandlerError("unsupported_signature","Тип аргумента пока не поддержан: "+type);
    }
    if(arguments.size()!=input_count)throw HandlerError("unknown_argument","Переданы лишние аргументы");
    if(strict_pod_contract)require_same_function_contract(pod_function_contract,capture_function_contract(object,function_owner,function,target_handle));
    for(const auto& [handle,expected]:object_arguments) {
        if(!is_compatible_object(resolve(handle),expected))
            throw HandlerError("incompatible_object_argument","Аргумент изменился до вызова функции");
    }
    for(const auto& argument:class_arguments) {
        if(!is_compatible_class(resolve(argument.handle),argument.property_class,argument.meta_class))
            throw HandlerError("incompatible_class_argument","Классовый аргумент изменился до вызова функции");
    }
    for(const auto& argument:enum_inputs) {
        const auto current=enum_layout(argument.type,argument.layout.size,argument.field);
        if(current.underlying_type!=argument.layout.underlying_type || current.size!=argument.layout.size
           || current.object!=argument.layout.object)
            throw HandlerError("unsupported_signature","Раскладка enum аргумента изменилась до вызова");
        if(argument.enum_identity) {
            const auto table=enum_table(current.object);
            const auto& before=*argument.enum_identity;
            if(!same_serial_identity(serial_identity(before),serial_identity(table.identity)))
                throw HandlerError("unsupported_signature","Объявленный UEnum изменился до вызова");
            if(!enum_arguments::contains_value(argument.value,table.entries))
                throw HandlerError("unknown_enum_value","Enum аргумент больше не объявлен перед вызовом");
        }
    }
    for(const auto& input:name_inputs) {
        if(field_type(input.field,[this](uint64_t value){return name(value);})!="NameProperty"
           || read<int32_t>(input.field+0x44)!=input.offset)
            throw HandlerError("unsupported_signature","Метаданные аргумента FName изменились до вызова");
        validate_name_argument_layout(read<int32_t>(input.field+0x34),read<int32_t>(input.field+0x30));
        if(!(capture_name(input.source.source)==input.source))
            throw HandlerError("name_source_changed","Значение или метаданные источника FName изменились до вызова");
    }
    const auto invocation_after=validate_object(object);
    if(!same_serial_identity(serial_identity(invocation_identity),serial_identity(invocation_after)))
        throw HandlerError("stale_handle","Identity цели изменилась до вызова; получите новую ссылку");
    for(auto& input:string_inputs) {
        const auto constructor_contract=capture_function_contract(object,function_owner,function,target_handle);
        require_same_function_contract(pod_function_contract,constructor_contract);
        if(expected_token)require_same_function_contract(expected_contract,constructor_contract);
        StringHeader header{};
        if(!construct_string(base_+kStringConstruct,&header,input.source.c_str(),static_cast<int>(input.source.size()))) {
            string_ledger.mark_uncertain();string_quarantine_cause=1;
            extra_retained_count=1;
            extra_retained_bytes=header.capacity>0?static_cast<size_t>(header.capacity)*sizeof(char16_t):0;
            throw HandlerError("string_constructor_fault","Конструктор FString завершился native exception; владение неизвестно");
        }
        extra_retained_count=header.data?1:0;
        extra_retained_bytes=header.capacity>0?static_cast<size_t>(header.capacity)*sizeof(char16_t):0;
        string_quarantine_cause=4;
        string_ledger.add_input(header,input.source.size());
        extra_retained_count=extra_retained_bytes=0;string_quarantine_cause=0;input.header=header;
        std::memcpy(buffer+input.offset,&header,sizeof(header));
    }
    validate_array_elements();
    for(auto& input:array_inputs) {
        const auto allocator_contract=capture_function_contract(object,function_owner,function,target_handle);
        require_same_function_contract(pod_function_contract,allocator_contract);
        if(expected_token)require_same_function_contract(expected_contract,allocator_contract);
        const auto count=static_cast<int32_t>(input.elements.size());ArrayHeader header{};
        if(count) {
            void* memory{};const auto allocation_bytes=static_cast<size_t>(count)*sizeof(uintptr_t);
            if(!allocate_object_array(base_+kObjectArrayRealloc,allocation_bytes,&memory)) {
                array_ledger.mark_uncertain();array_quarantine_cause=1;
                extra_array_count=1;extra_array_bytes=allocation_bytes;
                throw HandlerError("array_allocator_fault","Realloc Object array завершился native exception; владение неизвестно");
            }
            header={reinterpret_cast<uintptr_t>(memory),count,count};
            extra_array_count=memory?1:0;extra_array_bytes=allocation_bytes;
        }
        array_quarantine_cause=4;array_ledger.add_input(header,input.elements.size());
        extra_array_count=extra_array_bytes=0;array_quarantine_cause=0;input.header=header;
        if(count) {
            for(size_t index=0;index<input.elements.size();++index)input.pointers[index]=input.elements[index].pointer;
            if(!copy_memory(reinterpret_cast<void*>(header.data),input.pointers.data(),static_cast<size_t>(count)*sizeof(uintptr_t)))
                throw HandlerError("invalid_memory","Игровой буфер Object array недоступен для заполнения");
        }
        std::memcpy(buffer+input.offset,&header,sizeof(header));
    }
    validate_array_elements();validate_array_inputs();
    validate_string_inputs();
    if(expected_token || strict_pod_contract) {
        auto current_class=read<uintptr_t>(reinterpret_cast<uintptr_t>(object)+0x10);
        uintptr_t current_function{},current_owner{};std::set<uintptr_t> current_classes;
        for(int depth=0;current_class && !current_function;current_class=read<uintptr_t>(current_class+0x40),++depth) {
            if(depth>=64 || !current_classes.insert(current_class).second)
                throw HandlerError("signature_changed","Цепочка владельцев функции изменилась до вызова");
            auto field=read<uintptr_t>(current_class+0x48);std::set<uintptr_t> current_fields;
            for(;field;field=read<uintptr_t>(field+0x28)) {
                if(current_fields.size()>=4096 || !current_fields.insert(field).second)
                    throw HandlerError("signature_changed","Цепочка функций изменилась до вызова");
                if(object_name(reinterpret_cast<void*>(field))==function_name
                   && contains(object_name(reinterpret_cast<void*>(read<uintptr_t>(field+0x10))),"Function")) {
                    current_function=field;current_owner=current_class;break;
                }
            }
        }
        if(current_function!=function || current_owner!=function_owner)
            throw HandlerError("signature_changed","Ближайшая реализация функции изменилась до вызова");
        const auto final_contract=capture_function_contract(object,function_owner,function,target_handle);
        if(expected_token)require_same_function_contract(expected_contract,final_contract);
        if(strict_pod_contract)require_same_function_contract(pod_function_contract,final_contract);
    }
    string_event_started=true;
    native_exception::Diagnostic native_fault{};
    bool event_returned{};
    {
        as_exception_capture::Scope capture(script_errors);
        event_returned=call_event(reinterpret_cast<void*>(process_event_),object,reinterpret_cast<void*>(function),buffer,&native_fault);
    }
    if(!event_returned) {
        if(contains_string_input){string_ledger.mark_uncertain();string_quarantine_cause=2;}
        if(contains_object_array){array_ledger.mark_uncertain();array_quarantine_cause=2;}
        json details={{"executed",false},{"may_have_executed",true},{"do_not_retry",true},
            {"native_exception",native_exception::describe(native_fault)}};
        if(return_type=="StrProperty") {
            details["cleanup_attempted"]=false;details["cleanup_confirmed"]=false;
            details["cleanup_state"]="unknown";
        }
        throw HandlerError("game_call_fault","Игровой вызов вызвал native exception; эффект неизвестен",std::move(details));
    }
    string_event_completed=true;
    if(script_errors.exception_count) {
        json details={{"executed",true},{"may_have_executed",true},{"do_not_retry",true},
            {"process_event_returned",true},{"script_completion_confirmed",false},
            {"script_errors",as_exception_capture::describe(script_errors)}};
        if(return_type=="StrProperty")details["return_cleanup_confirmed"]=false;
        if(contains_string_input) {
            try {validate_string_inputs();}
            catch(const HandlerError& error) {string_ledger.mark_uncertain();string_quarantine_cause=3;details["cleanup_error"]=error.code();}
            catch(...) {string_ledger.mark_uncertain();string_quarantine_cause=3;details["cleanup_error"]="input_validation_failed";}
            const auto cleanup=release_strings();
            details["input_cleanup_confirmed"]=cleanup.confirmed;
            details["cleanup_confirmed"]=cleanup.confirmed;
            details["cleanup_attempted"]=cleanup.attempted;
        }
        if(contains_object_array) {
            try {validate_array_inputs();}
            catch(const HandlerError& error) {array_ledger.mark_uncertain();array_quarantine_cause=3;details["cleanup_error"]=error.code();}
            catch(...) {array_ledger.mark_uncertain();array_quarantine_cause=3;details["cleanup_error"]="input_validation_failed";}
            const auto cleanup=release_arrays();
            details["array_input_cleanup_confirmed"]=cleanup.confirmed;
            details["cleanup_confirmed"]=cleanup.confirmed;
            details["cleanup_attempted"]=cleanup.attempted;
        }
        throw HandlerError("angelscript_exception","Во время ProcessEvent зарегистрировано исключение AngelScript",std::move(details));
    }
    validate_array_inputs();
    validate_string_inputs();
    json result,return_enum,result_metadata=nullptr,out_parameters=json::object(),out_parameter_metadata=json::object();
    auto decode_vector_outputs=[&] {
        if(vector_parameters.empty())return;
        const auto current_target=validate_object(object);
        if(!same_serial_identity(serial_identity(invocation_identity),serial_identity(current_target)))
            throw HandlerError("stale_handle","Цель изменилась при получении FVector результата");
        for(const auto& vector:vector_parameters) {
            if(!vector.output && !vector.returned)continue;
            if(read<int32_t>(vector.field+0x44)!=vector.offset || read<uint64_t>(vector.field+0x38)!=vector.flags)
                throw HandlerError("vector_metadata_changed","FVector параметр изменился после вызова");
            const auto before=capture_vector_parameter(vector.field,vector.size,1);
            if(!(before==vector.layout))throw HandlerError("vector_metadata_changed","FVector layout изменился после вызова");
            std::array<std::byte,24> copied{};
            if(!copy_memory(copied.data(),buffer+vector.offset,static_cast<size_t>(vector.size)))
                throw HandlerError("invalid_struct_copy","FVector результат не скопирован");
            const auto value=vector_arguments::decode(vector.layout.layout,std::span(copied).first(vector.size));
            if(!(capture_vector_parameter(vector.field,vector.size,1)==vector.layout)
               || read<int32_t>(vector.field+0x44)!=vector.offset || read<uint64_t>(vector.field+0x38)!=vector.flags)
                throw HandlerError("vector_metadata_changed","FVector layout изменился при копировании результата");
            if(vector.returned){result=value;result_metadata=vector_snapshot_json(vector.layout,false);}
            else {out_parameters[vector.key]=value;out_parameter_metadata[vector.key]=vector_snapshot_json(vector.layout,false);}
        }
    };
    auto decode_color_outputs=[&] {
        for(const auto& color:color_parameters) {
            if(!color.returned)continue;
            if(read<int32_t>(color.field+0x44)!=color.offset || read<uint64_t>(color.field+0x38)!=color.flags
               || !(capture_linear_color_parameter(color.field,16,1)==color.layout))
                throw HandlerError("linear_color_metadata_changed","FLinearColor возврат изменился после вызова");
            std::array<std::byte,16> copied{};
            if(!copy_memory(copied.data(),buffer+color.offset,copied.size()))
                throw HandlerError("invalid_struct_copy","FLinearColor результат не скопирован");
            result=linear_color_arguments::decode(color.layout.layout,copied);
            if(!(capture_linear_color_parameter(color.field,16,1)==color.layout)
               || read<int32_t>(color.field+0x44)!=color.offset || read<uint64_t>(color.field+0x38)!=color.flags)
                throw HandlerError("linear_color_metadata_changed","FLinearColor metadata изменились при копировании результата");
            result_metadata=linear_color_snapshot_json(color.layout,false);
        }
    };
    auto decode_transform_return=[&] {
        if(!transform_return)return;
        const auto& saved=*transform_return;
        if(read<int32_t>(saved.field+0x44)!=saved.offset
           || !(capture_transform_parameter(saved.field,96,1)==saved.layout))
            throw HandlerError("signature_changed","Transform возврат изменился после вызова");
        std::array<std::byte,96> copied{};
        if(!copy_memory(copied.data(),buffer+saved.offset,copied.size()))
            throw HandlerError("invalid_struct_copy","Transform результат не скопирован");
        const auto value=transform_arguments::decode(saved.layout.layout,copied);
        if(read<int32_t>(saved.field+0x44)!=saved.offset
           || !(capture_transform_parameter(saved.field,96,1)==saved.layout))
            throw HandlerError("signature_changed","Transform metadata изменились при копировании результата");
        result=value;result_metadata=transform_snapshot_json(saved.layout,false);
    };
    auto decode_scalar_outputs=[&] {
        if(scalar_parameters.empty())return;
        if(!same_serial_identity(serial_identity(invocation_identity),serial_identity(validate_object(object))))
            throw HandlerError("stale_handle","Цель изменилась при получении скалярного out результата");
        require_same_function_contract(pod_function_contract,capture_function_contract(object,function_owner,function,target_handle));
        for(const auto& scalar:scalar_parameters) {
            if(read<int32_t>(scalar.field+0x44)!=scalar.offset || read<uint64_t>(scalar.field+0x38)!=scalar.flags)
                throw HandlerError("signature_changed","Скалярный out параметр изменился после вызова");
            const auto current_type=field_type(scalar.field,[this](auto value){return name(value);});
            const auto before=capture_scalar_output_layout(scalar.field,current_type,read<int32_t>(scalar.field+0x34),read<int32_t>(scalar.field+0x30));
            if(!(before==scalar.layout))throw HandlerError("signature_changed","Скалярная out раскладка изменилась после вызова");
            std::array<std::byte,8> copied{};
            if(!copy_memory(copied.data(),buffer+scalar.offset,static_cast<size_t>(scalar.layout.element_size)))
                throw HandlerError("invalid_scalar_copy","Скалярный out результат не скопирован");
            const auto value=scalar_outputs::decode(scalar.layout,std::span(copied).first(scalar.layout.element_size));
            const auto after=capture_scalar_output_layout(scalar.field,field_type(scalar.field,[this](auto packed){return name(packed);}),
                read<int32_t>(scalar.field+0x34),read<int32_t>(scalar.field+0x30));
            if(!(after==scalar.layout) || read<int32_t>(scalar.field+0x44)!=scalar.offset || read<uint64_t>(scalar.field+0x38)!=scalar.flags)
                throw HandlerError("signature_changed","Скалярная out раскладка изменилась при копировании результата");
            out_parameters[scalar.key]=value;out_parameter_metadata[scalar.key]=scalar_output_metadata(scalar.layout);
        }
        require_same_function_contract(pod_function_contract,capture_function_contract(object,function_owner,function,target_handle));
        if(!same_serial_identity(serial_identity(invocation_identity),serial_identity(validate_object(object))))
            throw HandlerError("stale_handle","Цель изменилась при копировании скалярного out результата");
    };
    auto decode_scalar_struct_return=[&] {
        if(!scalar_struct_return)return;
        const auto& saved=*scalar_struct_return;
        if(!same_serial_identity(serial_identity(invocation_identity),serial_identity(validate_object(object))))
            throw HandlerError("stale_handle","Цель изменилась при получении DTO результата");
        const auto before=capture_scalar_struct(saved.field,saved.element_size,saved.array_dim);
        if(!(before==saved))throw HandlerError("signature_changed","DTO возврат изменился после вызова");
        std::vector<std::byte> copied(static_cast<size_t>(saved.element_size));
        if(!copy_memory(copied.data(),buffer+saved.offset,copied.size()))
            throw HandlerError("invalid_struct_copy","DTO результат не скопирован");
        const auto value=saved.path=="/Script/CoreUObject.Rotator"?
            rotator_arguments::decode(saved.layout,copied):scalar_struct_values::decode(saved.layout,copied);
        if(!(capture_scalar_struct(saved.field,saved.element_size,saved.array_dim)==saved))
            throw HandlerError("signature_changed","DTO metadata изменились при копировании результата");
        result=value;result_metadata=scalar_struct_snapshot_json(saved,false);
    };
    auto decode_pod_outputs=[&] {
        auto require_transform_result_contract=[&] {
            if(!contains_transform && !contains_color && !contains_rotator && !contains_scalar_struct && !contains_string_input && !contains_object_array)return;
            if(!same_serial_identity(serial_identity(invocation_identity),serial_identity(validate_object(object))))
                throw HandlerError("stale_handle","Цель изменилась при обработке POD результата вызова");
            require_same_function_contract(pod_function_contract,capture_function_contract(object,function_owner,function,target_handle));
        };
        require_transform_result_contract();
        decode_vector_outputs();decode_color_outputs();decode_transform_return();decode_scalar_outputs();decode_scalar_struct_return();
        require_transform_result_contract();
    };
    auto append_pod_results=[&](json response) {
        if(!result_metadata.is_null())response["result_metadata"]=result_metadata;
        if(!out_parameters.empty()) {
            response["out_parameters"]=out_parameters;response["out_parameter_metadata"]=out_parameter_metadata;
        }
        return response;
    };
    if(return_type=="StrProperty") {
        StringHeader header{};
        if(!copy_memory(&header,buffer+return_offset,sizeof(header)))
            throw HandlerError("game_result_error","Вызов выполнен, но FString не прочитан; очистка не подтверждена",
                               string_cleanup_details(false,false,"invalid_string_storage"));
        if(contains_string_input) {
            string_ledger.add_return(header);string_return_registered=true;
            result=copied_string(header);
            decode_pod_outputs();validate_string_inputs();
            const auto cleanup=release_strings();
            if(!cleanup.confirmed)throw HandlerError("game_result_error","Освобождение FString не подтверждено; не повторять запрос");
            return append_pod_results({{"function",function_name},{"return_type",return_type},{"result",result},{"executed",true},
                {"thread_id",GetCurrentThreadId()},{"cleanup_confirmed",true},{"input_cleanup_confirmed",true}});
        }
        result=copied_owned_string_return(header,
            [](const StringHeader& copied){return copied_string(copied);},
            [this](uintptr_t memory){return free_string(base_+kFree,reinterpret_cast<void*>(memory));});
        try{decode_pod_outputs();}catch(const HandlerError& error) {
            throw HandlerError("game_result_error","Вызов выполнен, но POD out не прочитан; не повторять запрос",
                {{"executed",true},{"may_have_executed",true},{"do_not_retry",true},{"cause_code",error.code()},{"cleanup_confirmed",true}});
        } catch(...) {
            throw HandlerError("game_result_error","Вызов выполнен, но POD out не прочитан; не повторять запрос",
                {{"executed",true},{"may_have_executed",true},{"do_not_retry",true},{"cause_code","result_processing_failed"},{"cleanup_confirmed",true}});
        }
        return append_pod_results({{"function",function_name},{"return_type",return_type},{"result",result},
                {"executed",true},{"thread_id",GetCurrentThreadId()},{"cleanup_confirmed",true}});
    }
    try {
        if(return_type=="StructProperty") {
            // POD-результат копируется по сохранённой отражённой раскладке ниже.
        } else if(return_type=="NameProperty") {
            result=copied_name_return(return_size,1,
                [&]{return read<uint64_t>(reinterpret_cast<uintptr_t>(buffer+return_offset));},
                [this](uint64_t copied){return name(copied);});
        } else if(return_type=="EnumProperty" || return_type=="ByteProperty") {
            const auto returned=enum_property(reinterpret_cast<uintptr_t>(buffer+return_offset),return_type,return_size,return_field);
            result=returned.at("value");if(returned.contains("enum"))return_enum=returned.at("enum");
        } else result=return_field?property_value(reinterpret_cast<uintptr_t>(buffer+return_offset),return_type,return_size,return_field):json(nullptr);
        decode_pod_outputs();
    }
    catch(const HandlerError& error) {
        throw HandlerError("game_result_error","Вызов выполнен, но результат не получен; не повторять запрос",
            {{"executed",true},{"may_have_executed",true},{"do_not_retry",true},{"cause_code",error.code()}});
    } catch(...) {
        throw HandlerError("game_result_error","Вызов выполнен, но результат не получен; не повторять запрос",
            {{"executed",true},{"may_have_executed",true},{"do_not_retry",true},{"cause_code","result_processing_failed"}});
    }
    json response={{"function",function_name},{"return_type",return_type},{"result",result},{"executed",true},{"thread_id",GetCurrentThreadId()}};
    if(!return_enum.is_null())response["return_enum"]=return_enum;
    if(contains_string_input) {
        validate_string_inputs();const auto cleanup=release_strings();
        if(!cleanup.confirmed)throw HandlerError("game_result_error","Освобождение FString входов не подтверждено; не повторять запрос");
        response["cleanup_confirmed"]=true;response["input_cleanup_confirmed"]=true;
    }
    if(contains_object_array) {
        validate_array_inputs();const auto cleanup=release_arrays();
        if(!cleanup.confirmed)throw HandlerError("game_result_error","Освобождение Object array входов не подтверждено; повтор запрещён");
        response["cleanup_confirmed"]=true;response["array_input_cleanup_confirmed"]=true;
    }
    return append_pod_results(std::move(response));
    } catch(const HandlerError& error) {
        if(error.code()=="angelscript_exception")throw;
        if(contains_object_array) {
            if(string_event_completed && !array_cleanup_done && !array_ledger.uncertain()) {
                try{validate_array_inputs();}
                catch(...) {array_ledger.mark_uncertain();array_quarantine_cause=3;}
            }
            const auto cleanup=release_arrays();
            json details={{"executed",string_event_completed},{"may_have_executed",string_event_started},
                {"do_not_retry",string_event_started || !cleanup.confirmed},{"cause_code",error.code()},
                {"cleanup_attempted",cleanup.attempted},{"cleanup_confirmed",cleanup.confirmed},
                {"array_input_cleanup_confirmed",cleanup.confirmed},{"cleanup_state",cleanup.confirmed?"released":"unknown"},
                {"object_array_input_quarantined",object_array_inputs_quarantined_.load()}};
            if(script_errors.exception_count) {
                details["script_errors"]=as_exception_capture::describe(script_errors);
                details["script_completion_confirmed"]=false;
            }
            if(string_event_started || !cleanup.confirmed)
                throw HandlerError("game_result_error",string_event_completed?"Вызов выполнен; обработка или очистка Object array не завершена":
                    string_event_started?"Эффект вызова неизвестен; владение Object array не подтверждено":"Вызов не выполнен; владение Object array не подтверждено",std::move(details));
            throw HandlerError(error.code(),error.what(),std::move(details));
        }
        if(!contains_string_input)throw;
        if(string_event_completed && !string_cleanup_done && !string_ledger.uncertain()) {
            try{validate_string_inputs();}
            catch(...) {string_ledger.mark_uncertain();string_quarantine_cause=3;}
        }
        if(!script_errors.exception_count && string_event_completed && !string_cleanup_done && return_type=="StrProperty" && !string_return_registered && !string_ledger.uncertain()) {
            StringHeader header{};
            try {
                if(!copy_memory(&header,buffer+return_offset,sizeof(header)))throw HandlerError("invalid_string_storage","FString результат недоступен");
                string_ledger.add_return(header);string_return_registered=true;
            } catch(...) {string_ledger.mark_uncertain();string_quarantine_cause=4;}
        }
        const auto cleanup=release_strings();
        json details={{"executed",string_event_completed},{"may_have_executed",string_event_started},{"do_not_retry",string_event_started || !cleanup.confirmed},
            {"cause_code",error.code()},{"cleanup_attempted",cleanup.attempted},{"cleanup_confirmed",cleanup.confirmed},
            {"input_cleanup_confirmed",cleanup.confirmed},{"cleanup_state",cleanup.confirmed?"released":"unknown"},
            {"string_input_quarantined",string_inputs_quarantined_.load()}};
        if(script_errors.exception_count) {
            details["script_errors"]=as_exception_capture::describe(script_errors);
            details["script_completion_confirmed"]=false;
            if(return_type=="StrProperty")details["return_cleanup_confirmed"]=false;
        }
        if(string_event_started || !cleanup.confirmed)
            throw HandlerError("game_result_error",string_event_completed?"Вызов выполнен; обработка или очистка FString не завершена":
                string_event_started?"Эффект вызова неизвестен; владение FString не подтверждено":"Вызов не выполнен; владение FString не подтверждено",std::move(details));
        throw HandlerError(error.code(),error.what(),std::move(details));
    } catch(...) {
        if(contains_object_array) {
            if(string_event_completed && !array_cleanup_done && !array_ledger.uncertain()) {
                try{validate_array_inputs();}
                catch(...) {array_ledger.mark_uncertain();array_quarantine_cause=3;}
            }
            const auto cleanup=release_arrays();
            if(script_errors.exception_count)
                throw HandlerError("angelscript_exception","Исключение AngelScript зарегистрировано; подготовка полного отчёта не завершена",
                    {{"executed",true},{"may_have_executed",true},{"do_not_retry",true},{"process_event_returned",string_event_completed},
                     {"script_completion_confirmed",false},{"script_errors",{{"source","observed_logging_entry"},
                        {"exception_count",script_errors.exception_count},{"full_capture",false},{"messages",json::array()},{"stack_captured",false}}},
                     {"array_input_cleanup_confirmed",cleanup.confirmed},{"cleanup_confirmed",cleanup.confirmed}});
            throw HandlerError("game_result_error","Обработка Object array не завершена; повтор запрещён",
                {{"executed",string_event_completed},{"may_have_executed",string_event_started},{"do_not_retry",true},
                 {"cause_code","result_processing_failed"},{"cleanup_attempted",cleanup.attempted},{"cleanup_confirmed",cleanup.confirmed},
                 {"array_input_cleanup_confirmed",cleanup.confirmed},{"cleanup_state",cleanup.confirmed?"released":"unknown"},
                 {"object_array_input_quarantined",object_array_inputs_quarantined_.load()}});
        }
        if(!contains_string_input) {
            if(script_errors.exception_count)
                throw HandlerError("angelscript_exception","Исключение AngelScript зарегистрировано; подготовка полного отчёта не завершена",
                    {{"executed",true},{"may_have_executed",true},{"do_not_retry",true},{"process_event_returned",string_event_completed},
                     {"script_completion_confirmed",false},{"script_errors",{{"source","observed_logging_entry"},
                        {"exception_count",script_errors.exception_count},{"full_capture",false},{"messages",json::array()},{"stack_captured",false}}}});
            throw;
        }
        if(string_event_completed && !string_cleanup_done && !string_ledger.uncertain()) {
            try{validate_string_inputs();}
            catch(...) {string_ledger.mark_uncertain();string_quarantine_cause=3;}
        }
        if(!script_errors.exception_count && string_event_completed && !string_cleanup_done && return_type=="StrProperty" && !string_return_registered) {
            string_ledger.mark_uncertain();string_quarantine_cause=4;
        }
        const auto cleanup=release_strings();
        if(script_errors.exception_count)
            throw HandlerError("angelscript_exception","Исключение AngelScript зарегистрировано; подготовка полного отчёта не завершена",
                {{"executed",true},{"may_have_executed",true},{"do_not_retry",true},{"process_event_returned",string_event_completed},
                 {"script_completion_confirmed",false},{"script_errors",{{"source","observed_logging_entry"},
                    {"exception_count",script_errors.exception_count},{"full_capture",false},{"messages",json::array()},{"stack_captured",false}}},
                 {"input_cleanup_confirmed",cleanup.confirmed},{"cleanup_confirmed",cleanup.confirmed},
                 {"return_cleanup_confirmed",return_type!="StrProperty"}});
        throw HandlerError("game_result_error","Обработка FString не завершена; повтор запрещён",
            {{"executed",string_event_completed},{"may_have_executed",string_event_started},{"do_not_retry",true},{"cause_code","result_processing_failed"},
             {"cleanup_attempted",cleanup.attempted},{"cleanup_confirmed",cleanup.confirmed},{"input_cleanup_confirmed",cleanup.confirmed},
             {"cleanup_state",cleanup.confirmed?"released":"unknown"},{"string_input_quarantined",string_inputs_quarantined_.load()}});
    }
}
json GameAccess::execute(const json& request) {
    auto op=request.at("op").get<std::string>();auto params=request.at("params");
    auto session=request.at("session_id").get<std::string>();
    if(session!=handle_session_){observations_.disconnect(handle_session_);handles_.clear();signature_contracts_.clear();handle_session_=session;}
    const auto now=static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count());
    if(op=="snapshot") {
        if(!params.contains("targets"))throw HandlerError("invalid_snapshot_params","Требуется targets");
        return snapshot(params.at("targets"));
    }
    if(op=="watch_start") {
        if(!params.contains("handle") || !params.contains("properties"))
            throw HandlerError("invalid_watch_params","Требуются handle и properties");
        auto targets=json::array({{{"handle",params.at("handle")},{"properties",params.at("properties")}}});
        Observations::validate_targets(targets);
        return observations_.start(session,params,snapshot(targets),now,ticks_.load());
    }
    if(op=="watch_read")return observations_.read(session,params);
    if(op=="watch_stop")return observations_.stop(session,params,now,ticks_.load());
    if(op=="watch_forget")return observations_.forget(session,params);
    if(op!="objects" && op!="properties" && op!="functions" && op!="invoke" && op!="read_array" && op!="read_struct")throw HandlerError("unknown_operation","Операция не поддержана");
    if(op=="objects") {
        auto query=params.value("query",std::string{}),class_query=params.value("class_query",std::string{});int limit=limit_of(params);if(query.size()>256||class_query.size()>256)throw HandlerError("invalid_query","Запрос слишком длинный");
        if(params.contains("exact_path") && !params.at("exact_path").is_string())
            throw HandlerError("invalid_query","exact_path требует строку");
        const auto exact_path=params.value("exact_path",std::string{});
        if(!object_query::valid_exact_path(exact_path))
            throw HandlerError("invalid_query","exact_path превышает 8 КиБ или содержит NUL");
        auto count=read<int32_t>(base_+kObjects+0x14);if(count<0||count>4000000)throw HandlerError("invalid_registry","Некорректный реестр объектов");
        json items=json::array();int scanned=0,skipped=0;
        for(int index=0;index<count;++index){++scanned;
            try {
                auto object=slot(index);if(!object)continue;
                const auto ptr=reinterpret_cast<uintptr_t>(object);
                if(read<int32_t>(ptr+0xc)!=index)continue;
                const auto n=object_name(object),c=object_name(reinterpret_cast<void*>(read<uintptr_t>(ptr+0x10)));
                if(!contains(c,class_query))continue;
                if(!contains(n,query) && !object_query::query_uses_path(query))continue;
                if(!object_query::exact_path_may_match_name(exact_path,n))continue;
                const auto path=object_query::needs_path(query,exact_path,n)?object_path(object):std::string{};
                if(!object_query::matches(query,exact_path,n,path))continue;
                items.push_back(object_info(object,index));
                if(items.size()>=static_cast<size_t>(limit))break;
            }
            catch(const HandlerError& error){if(error.code()=="handle_limit")throw;++skipped;}
        }
        return {{"items",items},{"total_slots",count},{"scanned_slots",scanned},{"skipped",skipped},{"truncated",scanned<count},{"thread_id",GetCurrentThreadId()}};
    }
    if(op=="read_array")return read_array(params);
    if(op=="properties" && params.contains("property_names"))selected_property_query::validate(params.at("property_names"));
    auto object=resolve(params.at("handle").get<std::string>());
    if(op=="read_struct") {
        if(!params.contains("property_name") || !params.at("property_name").is_string())
            throw HandlerError("invalid_struct_params","Требуется строковое имя свойства");
        return read_struct(object,params.at("handle").get<std::string>(),params.at("property_name").get<std::string>());
    }
    if(op=="properties") {
        if(!params.contains("property_names"))return properties(object,limit_of(params));
        const auto before=serial_identity(validate_object(object));
        auto result=selected_properties(object,params.at("property_names"));
        if(resolve(params.at("handle").get<std::string>())!=object || !same_serial_identity(before,serial_identity(validate_object(object))))
            throw HandlerError("stale_handle","Объект изменился во время чтения выбранных свойств");
        result["items"]=std::move(result["properties"]);result.erase("properties");
        result["validation"]="game_thread_memory_read";return result;
    }
    if(op=="functions") {
        if(params.contains("function_query") && !params.at("function_query").is_string())
            throw HandlerError("invalid_query","function_query требует строку");
        const auto query=params.value("function_query",std::string{});
        if(!function_query::valid(query))throw HandlerError("invalid_query","function_query превышает 512 байт или содержит NUL");
        return functions(object,limit_of(params),query,params.at("handle").get<std::string>());
    }
    if(op=="invoke")return invoke(object,params.at("function").get<std::string>(),params.value("arguments",json::object()),
                                  params.at("handle").get<std::string>(),params.contains("expected_signature_token")?&params.at("expected_signature_token"):nullptr);
    throw HandlerError("unknown_operation","Операция не поддержана");
}
}
