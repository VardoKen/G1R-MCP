#include "g1r_mcp/game_access.hpp"
#include "g1r_mcp/transport.hpp"
#include "g1r_mcp/scalar_arguments.hpp"
#include "g1r_mcp/object_arguments.hpp"
#include "g1r_mcp/object_query.hpp"
#include <MinHook.h>
#include <bcrypt.h>
#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <set>
#include <vector>

namespace g1r_mcp {
using json = nlohmann::json;
namespace {
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
constexpr const char* kHash = "0e369f64ba50aeb85925119ded60e0449dbc4917e38b46cf7251e533d422820b";
constexpr uint8_t kTickBytes[] = {0x4c,0x8b,0xdc,0x55,0x57,0x41,0x54,0x49,0x8d,0xab,0x08,0xfc,0xff,0xff,0x48,0x81,0xec,0xe0,0x04,0x00,0x00};
constexpr uint8_t kEventBytes[] = {0x40,0x55,0x56,0x57,0x41,0x54,0x41,0x55,0x41,0x56,0x41,0x57,0x48,0x81,0xec,0x70,0x01,0x00,0x00,0x48,0x8d,0x6c,0x24,0x30};

bool copy_memory(void* destination, const void* source, size_t count) {
    if (!source || count > 1048576) return false;
    __try { std::memcpy(destination, source, count); return true; }
    __except(EXCEPTION_EXECUTE_HANDLER) { return false; }
}
template<class T> T read(uintptr_t address) {
    T value{};
    if (!copy_memory(&value, reinterpret_cast<void*>(address), sizeof(T)))
        throw HandlerError("invalid_memory", "Память объекта недоступна");
    return value;
}
bool call_event(void* function, void* object, void* event, void* params) {
    __try { reinterpret_cast<void(__fastcall*)(void*,void*,void*)>(function)(object,event,params); return true; }
    __except(EXCEPTION_EXECUTE_HANDLER) { return false; }
}
void free_string(uintptr_t free_address, void* memory) {
    if (!memory) return;
    __try { reinterpret_cast<void(__fastcall*)(void*)>(free_address)(memory); }
    __except(EXCEPTION_EXECUTE_HANDLER) { }
}
std::string utf8(const wchar_t* value, int length) {
    if (length <= 0) return {};
    int count = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value,length,nullptr,0,nullptr,nullptr);
    if (!count) throw HandlerError("invalid_string", "Некорректная строка UTF-16");
    std::string result(count,'\0');
    WideCharToMultiByte(CP_UTF8,WC_ERR_INVALID_CHARS,value,length,result.data(),count,nullptr,nullptr);
    return result;
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
        auto pointer=read<uintptr_t>(address);auto count=read<int32_t>(address+8),max=read<int32_t>(address+12);
        if(!pointer && !count) return "";
        if(count<0 || count>32768 || max<count) throw HandlerError("invalid_string","Некорректный FString");
        std::vector<wchar_t> text(count);
        if(count && !copy_memory(text.data(),reinterpret_cast<void*>(pointer),count*2ull)) throw HandlerError("invalid_memory","Строка недоступна");
        if(count && text.back()==0) text.pop_back();return utf8(text.data(),static_cast<int>(text.size()));
    }
    return nullptr;
}
}

GameAccess* GameAccess::active_{};
bool GameAccess::start() {
    if(enabled_) return true;
    if(file_hash()!=kHash) {startup_error_="exe_hash_mismatch";return false;}
    base_=reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));
    uint8_t tick[sizeof(kTickBytes)]{},event[sizeof(kEventBytes)]{};
    if(!copy_memory(tick,reinterpret_cast<void*>(base_+kTick),sizeof(tick)) ||
       !copy_memory(event,reinterpret_cast<void*>(base_+kProcessEvent),sizeof(event)) ||
       std::memcmp(tick,kTickBytes,sizeof(tick)) || std::memcmp(event,kEventBytes,sizeof(event))) {
        startup_error_="signature_mismatch";return false;
    }
    auto initialized=MH_Initialize();
    if(initialized!=MH_OK && initialized!=MH_ERROR_ALREADY_INITIALIZED) {startup_error_="hook_initialize_failed";return false;}
    active_=this;process_event_=reinterpret_cast<ProcessEventFn>(base_+kProcessEvent);
    diagnostic("hook_create_enter");
    if(MH_CreateHook(reinterpret_cast<void*>(base_+kTick),reinterpret_cast<void*>(&hooked_tick),reinterpret_cast<void**>(&original_tick_))!=MH_OK ||
       MH_EnableHook(reinterpret_cast<void*>(base_+kTick))!=MH_OK) {startup_error_="tick_hook_failed";return false;}
    diagnostic("hook_enabled");enabled_=true;return true;
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
    return {{"version","1.0.0-dev.4"},{"native_route","standalone_in_process"},{"hook_installed",enabled_.load()},
            {"game_thread_id",game_thread_.load()},{"tick_count",ticks_.load()},{"ready",enabled_.load()&&ticks_.load()>0},
            {"build_hash",kHash},{"startup_error",startup_error_},
            {"process_id",GetCurrentProcessId()},
            {"operations",{"status","enable_game","objects","properties","functions","invoke",
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
            {"limitations",{"Экспериментальная привязка к одному EXE","Serial UObject не подтверждён: handle проверяет слот, указатель, класс и имя","Одно активное соединение; ссылки и наблюдения принадлежат его сессии","Смена мира не определяет отдельное поколение; проверяется объект перед каждым чтением","Без выгрузки DLL: завершение через закрытие игры"}}};
}
json GameAccess::request(const json& envelope) {
    if(envelope.at("op")=="status") return status();
    if(!enabled_) throw HandlerError("native_not_ready","Перехват игры не установлен: "+startup_error_);
    auto job=std::make_shared<Job>();job->request=envelope;auto future=job->result.get_future();
    {std::lock_guard lock(queue_mutex_);if(queue_.size()>=8)throw HandlerError("queue_full","Очередь игрового потока заполнена");queue_.push_back(job);}
    if(future.wait_for(std::chrono::milliseconds(2500))!=std::future_status::ready) {
        int queued=0;
        if(job->state.compare_exchange_strong(queued,2)) throw HandlerError("game_not_pumping","Запрос отменён до начала: игровой Tick не обслужил очередь");
        throw HandlerError("game_timeout","Ожидание закончено; принятый вызов мог выполниться",
            {{"executed",false},{"may_have_executed",true},{"do_not_retry",true}});
    }
    return future.get();
}
void GameAccess::disconnect(const std::string& session) {
    // Сетевой поток: только пометка и отмена ещё не вошедших в Tick работ.
    std::lock_guard lock(queue_mutex_);
    session_cleanup_pending_=true;
    for (const auto& job : queue_) {
        if (job->request.value("session_id",std::string{})!=session) continue;
        int queued=0;
        if (job->state.compare_exchange_strong(queued,2)) {
            job->result.set_exception(std::make_exception_ptr(
                HandlerError("session_closed","Сессия отключилась до исполнения запроса")));
        }
    }
}
void GameAccess::pump() {
    if(!game_thread_.load()) game_thread_=GetCurrentThreadId();
    if(game_thread_.load()!=GetCurrentThreadId()) return;
    ++ticks_;
    std::shared_ptr<Job> job;
    {
        std::lock_guard lock(queue_mutex_);
        if(session_cleanup_pending_) {
            observations_.disconnect(handle_session_);
            handles_.clear();handle_session_.clear();session_cleanup_pending_=false;
        }
        if(!queue_.empty()){job=queue_.front();queue_.pop_front();}
    }
    if(job) {
        int queued=0;
        if(job->state.compare_exchange_strong(queued,1)) {
            try {job->result.set_value(execute(job->request));}catch(...) {job->result.set_exception(std::current_exception());}
            job->state=3;
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
            handles_.clear();handle_session_.clear();session_cleanup_pending_=false;
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
void* GameAccess::slot(int index) const {
    auto count=read<int32_t>(base_+kObjects+0x14);auto chunks=read<uintptr_t>(base_+kObjects);
    if(count<0 || count>4000000 || index<0 || index>=count)return nullptr;
    auto chunk_count=read<int32_t>(base_+kObjects+0x1c);
    if((index>>16)>=chunk_count || chunk_count<1 || chunk_count>64)return nullptr;
    auto chunk=read<uintptr_t>(chunks+(index>>16)*8ull);
    return reinterpret_cast<void*>(read<uintptr_t>(chunk+(index&65535)*24ull));
}
std::string GameAccess::handle_for(void* object,int index) {
    const auto identity=validate_object(object);
    if(identity.index!=index)throw HandlerError("stale_handle","Индекс объекта изменился");
    for(const auto& [id,existing]:handles_) {
        if(same_object_identity({reinterpret_cast<uintptr_t>(existing.object),reinterpret_cast<uintptr_t>(existing.object_class),existing.index,existing.name},
                                {reinterpret_cast<uintptr_t>(identity.object),reinterpret_cast<uintptr_t>(identity.object_class),identity.index,identity.name}))return id;
    }
    if(handles_.size()>=4096)throw HandlerError("handle_limit","Реестр ссылок заполнен; существующие ссылки сохранены");
    auto id="obj-"+std::to_string(++next_handle_);
    handles_[id]=identity;return id;
}
GameAccess::Handle GameAccess::validate_object(void* object) const {
    if(!object)throw HandlerError("invalid_object","Указатель объекта отсутствует");
    const auto ptr=reinterpret_cast<uintptr_t>(object);
    const auto index=read<int32_t>(ptr+0xc);
    if(index<0 || slot(index)!=object)throw HandlerError("stale_handle","Объект не соответствует слоту реестра");
    if(read<uint32_t>(ptr+8)&0x18000)throw HandlerError("object_destroying","Объект уничтожается");
    const auto object_class=read<uintptr_t>(ptr+0x10);
    if(!object_class)throw HandlerError("invalid_object_class","У объекта отсутствует класс");
    const auto class_index=read<int32_t>(object_class+0xc);
    if(class_index<0 || slot(class_index)!=reinterpret_cast<void*>(object_class)
       || (read<uint32_t>(object_class+8)&0x18000))
        throw HandlerError("invalid_object_class","Класс объекта не соответствует живому слоту");
    const auto object_fname=read<uint64_t>(ptr+0x18);
    name(object_fname);name(read<uint64_t>(object_class+0x18));
    return {object,reinterpret_cast<void*>(object_class),index,object_fname};
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
    if(!same_object_identity({reinterpret_cast<uintptr_t>(identity.object),reinterpret_cast<uintptr_t>(identity.object_class),identity.index,identity.name},
                             {reinterpret_cast<uintptr_t>(after.object),reinterpret_cast<uintptr_t>(after.object_class),after.index,after.name}))
        throw HandlerError("stale_handle","Объектное значение изменилось при копировании");
    return result;
}
json GameAccess::property_value(uintptr_t address,const std::string& type,int size,uintptr_t field) {
    if(type=="ClassProperty")throw HandlerError("unsupported_property_type","ClassProperty пока не поддержан");
    if(type=="ObjectProperty") {
        if(size!=sizeof(uintptr_t))throw HandlerError("unsupported_property_layout","ObjectProperty требует указатель x64");
        const auto expected_class=reinterpret_cast<void*>(read<uintptr_t>(field+0x70));
        return object_descriptor(reinterpret_cast<void*>(read<uintptr_t>(address)),expected_class);
    }
    return scalar(address,type,size,field);
}
void* GameAccess::resolve(const std::string& id) {
    auto found=handles_.find(id);if(found==handles_.end())throw HandlerError("stale_handle","Ссылка неизвестна или истекла");
    auto& h=found->second;auto ptr=reinterpret_cast<uintptr_t>(h.object);
    if(slot(h.index)!=h.object || read<int32_t>(ptr+0xc)!=h.index || read<uintptr_t>(ptr+0x10)!=reinterpret_cast<uintptr_t>(h.object_class) || read<uint64_t>(ptr+0x18)!=h.name)
        throw HandlerError("stale_handle","Объект ссылки изменился");
    if(read<uint32_t>(ptr+8)&0x18000)throw HandlerError("object_destroying","Объект начал или завершил уничтожение");
    validate_object(h.object);
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
                    auto value=property_value(reinterpret_cast<uintptr_t>(object)+offset,type,size,field);item["value"]=value;item["supported"]=type=="ObjectProperty" || !value.is_null();
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
    std::set<uintptr_t> visited_classes;
    for(int depth=0;klass && depth<64 && selected.size()<names.size();
        klass=read<uintptr_t>(klass+0x40),++depth) {
        if(!visited_classes.insert(klass).second){scan_truncated=true;break;}
        auto field=read<uintptr_t>(klass+0x50);auto class_size=read<int32_t>(klass+0x58);
        for(;field && selected.size()<names.size();field=read<uintptr_t>(field+0x18)) {
            if(++scanned>4096){scan_truncated=true;break;}
            const auto field_name=name(read<uint64_t>(field+0x20));
            if(selected.contains(field_name) || std::find(names.begin(),names.end(),json(field_name))==names.end())continue;
            const auto type=field_type(field,[this](auto f){return name(f);});
            const auto offset=read<int32_t>(field+0x44),size=read<int32_t>(field+0x34);
            json item={{"name",field_name},{"type",type},{"supported",false}};
            try {
                if(offset<0 || size<=0 || size>class_size || offset>class_size-size
                   || read<int32_t>(field+0x30)!=1)
                    throw HandlerError("unsupported_property_layout","Неподдерживаемая раскладка свойства");
                auto value=property_value(reinterpret_cast<uintptr_t>(object)+offset,type,size,field);
                if(value.is_null() && type!="ObjectProperty")throw HandlerError("unsupported_property_type","Тип свойства не поддержан");
                if(value.dump().size()>16384)throw HandlerError("property_value_too_large","Значение превышает предел поля 16 КиБ");
                item["value"]=std::move(value);item["supported"]=true;
            } catch(const HandlerError& error) {
                item["error"]={{"code",error.code()},{"message",error.what()}};
            }
            selected.emplace(field_name,std::move(item));
        }
        if(scan_truncated)break;
        if(depth==63 && klass)scan_truncated=true;
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
json GameAccess::functions(void* object,int limit) {
    json items=json::array();auto klass=read<uintptr_t>(reinterpret_cast<uintptr_t>(object)+0x10);bool truncated=false;
    for(int depth=0;klass && depth<64;klass=read<uintptr_t>(klass+0x40),++depth) {
        auto field=read<uintptr_t>(klass+0x48);
        for(int count=0;field && count<4096;field=read<uintptr_t>(field+0x28),++count) {
            auto fieldclass=read<uintptr_t>(field+0x10);if(!contains(object_name(reinterpret_cast<void*>(fieldclass)),"Function"))continue;
            if(items.size()>=static_cast<size_t>(limit)){truncated=true;break;}
            json parameters=json::array();auto parameter=read<uintptr_t>(field+0x50);
            for(int pc=0;parameter && pc<128;parameter=read<uintptr_t>(parameter+0x18),++pc) {
                auto pf=read<uint64_t>(parameter+0x38);if(!(pf&0x80))continue;
                parameters.push_back({{"name",name(read<uint64_t>(parameter+0x20))},
                    {"type",field_type(parameter,[this](auto f){return name(f);})},
                    {"offset",read<int32_t>(parameter+0x44)},{"element_size",read<int32_t>(parameter+0x34)},
                    {"return",(pf&0x400)!=0},{"out",(pf&0x100)!=0},{"reference",(pf&0x800)!=0}});
            }
            items.push_back({{"name",object_name(reinterpret_cast<void*>(field))},{"owner",object_name(reinterpret_cast<void*>(klass))},
                {"flags",read<uint32_t>(field+0xb0)},{"parameter_count",read<uint8_t>(field+0xb4)},
                {"parameter_size",read<uint16_t>(field+0xb6)},{"return_offset",read<uint16_t>(field+0xb8)},{"parameters",parameters}});
        } if(truncated)break;
    }
    return {{"items",items},{"truncated",truncated}};
}
json GameAccess::invoke(void* object,const std::string& function_name,const json& arguments) {
    auto klass=read<uintptr_t>(reinterpret_cast<uintptr_t>(object)+0x10);uintptr_t function=0;
    for(int depth=0;klass && depth<64 && !function;klass=read<uintptr_t>(klass+0x40),++depth) {
        auto field=read<uintptr_t>(klass+0x48);
        for(int count=0;field && count<4096;field=read<uintptr_t>(field+0x28),++count)
            if(object_name(reinterpret_cast<void*>(field))==function_name && contains(object_name(reinterpret_cast<void*>(read<uintptr_t>(field+0x10))),"Function")){function=field;break;}
    }
    if(!function)throw HandlerError("function_not_found","Отражённая функция не найдена");
    auto bytes=read<uint16_t>(function+0xb6);if(bytes>4096)throw HandlerError("unsupported_signature","Слишком большой буфер параметров");
    std::vector<uintptr_t> parameter_fields;
    std::set<uintptr_t> visited_fields;
    auto parameter=read<uintptr_t>(function+0x50);
    int extent=bytes;
    for(;parameter;parameter=read<uintptr_t>(parameter+0x18)) {
        if(visited_fields.size()>=128 || !visited_fields.insert(parameter).second)
            throw HandlerError("unsupported_signature","Цепочка отражённых параметров превысила предел или содержит цикл");
        const auto flags=read<uint64_t>(parameter+0x38);
        if(!(flags&0x80))continue;
        extent=parameter_extent(extent,read<int32_t>(parameter+0x44),
            read<int32_t>(parameter+0x34),read<int32_t>(parameter+0x30));
        parameter_fields.push_back(parameter);
    }
    std::vector<uint64_t> aligned((extent+7)/8+1,0);auto buffer=reinterpret_cast<unsigned char*>(aligned.data());
    uintptr_t return_field{};std::string return_type;int return_size{};int return_offset{};size_t input_count{};
    std::vector<std::pair<std::string,void*>> object_arguments;
    for(const auto field:parameter_fields) {
        auto flags=read<uint64_t>(field+0x38);if(!(flags&0x80))continue;
        auto type=field_type(field,[this](auto f){return name(f);});int offset=read<int32_t>(field+0x44),size=read<int32_t>(field+0x34);
        parameter_extent(extent,offset,size,read<int32_t>(field+0x30));
        if(flags&0x400){
            return_field=field;return_type=type;return_size=size;return_offset=offset;
            if(type=="ObjectProperty") {
                if(size!=8)throw HandlerError("unsupported_signature","Размер объектного возврата не поддержан");
                validate_class(reinterpret_cast<void*>(read<uintptr_t>(field+0x70)));
            } else if((type=="StrProperty" && size!=16)
               || (type!="StrProperty" && property_value(reinterpret_cast<uintptr_t>(buffer+offset),type,size,field).is_null()))
                throw HandlerError("unsupported_signature","Тип результата пока не поддержан");
            continue;
        }
        if(flags&(0x100|0x800))throw HandlerError("unsupported_signature","Out/ref параметры пока не поддержаны");
        auto key=name(read<uint64_t>(field+0x20));if(!arguments.contains(key))throw HandlerError("missing_argument","Не задан аргумент "+key);auto v=arguments.at(key);++input_count;
        if(type=="BoolProperty" && v.is_boolean()){auto bo=read<uint8_t>(field+0x71),mask=read<uint8_t>(field+0x73);validate_bool_layout(size,bo,mask);buffer[offset+bo]=v.get<bool>()?mask:0;}
        else if(type=="IntProperty" && size==4){*reinterpret_cast<int32_t*>(buffer+offset)=int32_argument(v);}
        else if(type=="FloatProperty" && size==4){*reinterpret_cast<float*>(buffer+offset)=float_argument(v);}
        else if(type=="DoubleProperty" && size==8){*reinterpret_cast<double*>(buffer+offset)=double_argument(v);}
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
        else throw HandlerError("unsupported_signature","Тип аргумента пока не поддержан: "+type);
    }
    if(arguments.size()!=input_count)throw HandlerError("unknown_argument","Переданы лишние аргументы");
    for(const auto& [handle,expected]:object_arguments) {
        if(!is_compatible_object(resolve(handle),expected))
            throw HandlerError("incompatible_object_argument","Аргумент изменился до вызова функции");
    }
    validate_object(object);
    if(!call_event(reinterpret_cast<void*>(process_event_),object,reinterpret_cast<void*>(function),buffer))throw HandlerError("game_call_fault","Игровой вызов вызвал native exception; эффект неизвестен",
        {{"executed",false},{"may_have_executed",true},{"do_not_retry",true}});
    json result;
    try {result=return_field?property_value(reinterpret_cast<uintptr_t>(buffer+return_offset),return_type,return_size,return_field):json(nullptr);}
    catch(const HandlerError& error) {
        if(return_type=="StrProperty")free_string(base_+kFree,*reinterpret_cast<void**>(buffer+return_offset));
        throw HandlerError("game_result_error","Вызов выполнен, но результат не получен; не повторять запрос",
            {{"executed",true},{"may_have_executed",true},{"do_not_retry",true},{"cause_code",error.code()}});
    } catch(...) {
        if(return_type=="StrProperty")free_string(base_+kFree,*reinterpret_cast<void**>(buffer+return_offset));
        throw HandlerError("game_result_error","Вызов выполнен, но результат не получен; не повторять запрос",
            {{"executed",true},{"may_have_executed",true},{"do_not_retry",true},{"cause_code","result_processing_failed"}});
    }
    if(return_type=="StrProperty")free_string(base_+kFree,*reinterpret_cast<void**>(buffer+return_offset));
    return {{"function",function_name},{"return_type",return_type},{"result",result},{"executed",true},{"thread_id",GetCurrentThreadId()}};
}
json GameAccess::execute(const json& request) {
    auto op=request.at("op").get<std::string>();auto params=request.at("params");
    auto session=request.at("session_id").get<std::string>();
    if(session!=handle_session_){observations_.disconnect(handle_session_);handles_.clear();handle_session_=session;}
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
    if(op!="objects" && op!="properties" && op!="functions" && op!="invoke")throw HandlerError("unknown_operation","Операция не поддержана");
    if(op=="objects") {
        auto query=params.value("query",std::string{}),class_query=params.value("class_query",std::string{});int limit=limit_of(params);if(query.size()>256||class_query.size()>256)throw HandlerError("invalid_query","Запрос слишком длинный");
        if(params.contains("exact_path") && !params.at("exact_path").is_string())
            throw HandlerError("invalid_query","exact_path требует строку");
        const auto exact_path=params.value("exact_path",std::string{});
        if(!object_query::valid_exact_path(exact_path))
            throw HandlerError("invalid_query","exact_path превышает 8 КиБ или содержит NUL");
        auto count=read<int32_t>(base_+kObjects+0x14);if(count<0||count>4000000)throw HandlerError("invalid_registry","Некорректный реестр объектов");
        json items=json::array();int scanned=0,skipped=0;
        for(int index=0;index<count;++index){++scanned;auto object=slot(index);if(!object)continue;
            try {
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
    auto object=resolve(params.at("handle").get<std::string>());
    if(op=="properties")return properties(object,limit_of(params));
    if(op=="functions")return functions(object,limit_of(params));
    if(op=="invoke")return invoke(object,params.at("function").get<std::string>(),params.value("arguments",json::object()));
    throw HandlerError("unknown_operation","Операция не поддержана");
}
}
