#include <g1r_mcp/observations.hpp>
#include <g1r_mcp/transport.hpp>
#include <g1r_mcp/scalar_arguments.hpp>
#include <g1r_mcp/object_arguments.hpp>
#include <iostream>
#include <stdexcept>
#include <string>

using nlohmann::json;
using g1r_mcp::Observations;
namespace {
void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
template<class Operation> void error(const char* code, Operation operation) {
    try { operation(); }
    catch (const g1r_mcp::HandlerError& caught) {
        require(caught.code() == code, "Неожиданный код отказа");
        return;
    }
    throw std::runtime_error("Ожидался отказ");
}
json params(int duration=100, int capacity=4) {
    return {{"handle","obj-1"},{"properties",{"Health"}},
            {"duration_ms",duration},{"interval_ms",10},{"capacity",capacity}};
}
json snapshot(int value) {
    return {{"snapshot_id","snapshot-1"},{"session_id","owner"},{"partial",false},{"truncated",false},
            {"objects",json::array({{{"handle","obj-1"},{"path","World.Actor"},{"class","Actor"},
               {"properties",json::array({{{"name","Health"},{"type","IntProperty"},
                                          {"supported",true},{"value",value}}})}}})}};
}
json query(const std::string& id, int after=0, int limit=100) {
    return {{"watch_id",id},{"after_seq",after},{"limit",limit}};
}

void changes_limits_and_stop() {
    Observations model;
    auto start=model.start("owner",params(100,2),snapshot(10),0,1);
    const auto id=start.at("watch_id").get<std::string>();
    require(start.at("start_seq")==1 && start.at("state")=="running", "Неверный старт");
    int calls=0;
    model.pump(9,2,[&](const json&){++calls;return snapshot(20);});
    require(calls==0,"Чтение до интервала");
    model.pump(10,3,[&](const json&){++calls;return snapshot(10);});
    require(calls==1,"Не выполнено due чтение");
    require(model.read("owner",query(id)).at("events").size()==1,"Неизменённое значение создало событие");
    model.pump(20,4,[&](const json&){++calls;return snapshot(20);});
    model.pump(30,5,[&](const json&){++calls;return snapshot(30);});
    auto events=model.read("owner",query(id));
    require(events.at("dropped_events")==1 && events.at("gap")==true,"Потеря baseline не сообщена");
    require(events.at("events").size()==2 && events.at("events")[0].at("seq")==2,"Неверный ring буфер");
    auto paged=model.read("owner",query(id,0,1));
    require(paged.at("truncated")==true && paged.at("cursor")==2,"Пагинация потеряла cursor");
    require(model.read("owner",query(id)).at("events").size()==2,"Чтение неожиданно опустошило буфер");
    error("foreign_watch",[&]{model.read("other",query(id));});
    error("watch_running",[&]{model.forget("owner",query(id));});
    const auto stopped=model.stop("owner",query(id),31,6);
    require(stopped.at("cleanup_complete")==true && stopped.at("state")=="stopped","Остановка не подтверждена");
    const auto last=stopped.at("last_seq");
    model.stop("owner",query(id),32,7);
    require(model.read("owner",query(id)).at("last_seq")==last,"Повтор stop создал второе terminal");
    model.pump(90,8,[&](const json&){++calls;return snapshot(99);});
    require(calls==3,"Чтение после stop");
    require(model.forget("owner",query(id)).at("forgotten")==true && model.size()==0,"Forget не очистил запись");
    error("unknown_watch",[&]{model.read("owner",query(id));});
}

void expiry_invalidation_and_disconnect() {
    Observations model;
    const auto id=model.start("owner",params(10),snapshot(1),0,1).at("watch_id").get<std::string>();
    int calls=0;
    model.pump(10,2,[&](const json&){++calls;return snapshot(2);});
    require(calls==0 && model.read("owner",query(id)).at("state")=="completed","Expiry прочитал уже законченную работу");
    model.pump(20,3,[&](const json&){++calls;return snapshot(2);});
    require(calls==0,"Чтение после expiry");
    const auto id2=model.start("owner",params(),snapshot(1),20,4).at("watch_id").get<std::string>();
    model.pump(30,5,[&](const json&){++calls;auto copy=snapshot(1);copy["partial"]=true;return copy;});
    require(model.read("owner",query(id2)).at("state")=="failed","Invalidation не завершила работу");
    model.pump(50,6,[&](const json&){++calls;return snapshot(3);});
    require(calls==1,"Чтение после invalidation");
    model.start("other",params(),snapshot(1),50,7);
    model.disconnect("owner");
    require(model.size()==1,"Disconnect затронул чужую работу или оставил свои записи");
    error("unknown_watch",[&]{model.read("owner",query(id2));});
    model.disconnect("other");
    model.pump(80,8,[&](const json&){++calls;return snapshot(3);});
    require(calls==1 && model.size()==0,"Disconnect оставил callback");
}

void two_watches_and_byte_bounds() {
    Observations model;
    auto one=model.start("owner",params(100,128),snapshot(1),0,1).at("watch_id").get<std::string>();
    auto two=model.start("owner",params(100,128),snapshot(1),0,1).at("watch_id").get<std::string>();
    int calls=0;
    model.pump(10,2,[&](const json&){++calls;return snapshot(2);});
    require(calls==1,"Pump превысил одну due работу");
    model.pump(11,3,[&](const json&){++calls;return snapshot(3);});
    require(calls==2,"Вторая работа не получила чтение");
    model.stop("owner",query(one),12,4);
    model.pump(21,5,[&](const json&){++calls;return snapshot(4);});
    require(calls==3 && model.read("owner",query(two)).at("state")=="running","Stop одной работы затронул другую");
    model.pump(31,6,[&](const json&){++calls;auto data=snapshot(4);data["objects"][0]["properties"][0]["value"]=std::string(300000,'a');return data;});
    const auto failed=model.read("owner",query(two));
    require(failed.at("state")=="failed" && failed.at("reason")=="event_too_large","Переполнение байтов не завершило работу");
    require(failed.at("buffer_bytes").get<std::size_t>()<=Observations::MaxBufferBytes,"Буфер превысил байтовый предел");
    model.pump(90,7,[&](const json&){++calls;return snapshot(9);});
    require(calls==4,"Чтение после byte failure");
}

void invalid_parameters_and_capacity() {
    Observations model;
    auto bad=params();bad["interval_ms"]=0;
    error("invalid_watch_params",[&]{model.start("owner",bad,snapshot(1),0,1);});
    bad=params();bad["duration_ms"]=true;
    error("invalid_watch_params",[&]{model.start("owner",bad,snapshot(1),0,1);});
    bad=params();bad["properties"]={"Health","Health"};
    error("invalid_snapshot_params",[&]{model.start("owner",bad,snapshot(1),0,1);});
    auto incomplete=snapshot(1);incomplete["partial"]=true;
    error("unsupported_watch_target",[&]{model.start("owner",params(),incomplete,0,1);});
    error("invalid_snapshot_params",[&]{Observations::validate_targets(json::array());});
    auto target=json{{"handle","obj-1"},{"properties",{"Health"}}};
    auto extra=target;extra["unknown"]=true;
    error("invalid_snapshot_params",[&]{Observations::validate_targets(json::array({extra}));});
    error("invalid_snapshot_params",[&]{Observations::validate_targets(json::array({target,target}));});
    auto long_handle=target;long_handle["handle"]=std::string(81,'x');
    error("invalid_snapshot_params",[&]{Observations::validate_targets(json::array({long_handle}));});
    auto non_string=target;non_string["properties"]={1};
    error("invalid_snapshot_params",[&]{Observations::validate_targets(json::array({non_string}));});
    auto too_many=target;too_many["properties"]=json::array();
    for(int i=0;i<33;++i)too_many["properties"].push_back(std::to_string(i));
    error("invalid_snapshot_params",[&]{Observations::validate_targets(json::array({too_many}));});
    for(std::size_t i=0;i<Observations::MaxWatches;++i)model.start("owner",params(),snapshot(1),0,1);
    error("watch_limit",[&]{model.start("owner",params(),snapshot(1),0,1);});
    error("invalid_watch_params",[&]{model.read("owner",query("watch-1",99));});
    model.disconnect("owner");
    require(model.size()==0,"Не очищены записи после лимита");
}

void copied_values_byte_drop_and_no_catchup() {
    Observations model;
    auto baseline=snapshot(1);
    const auto id=model.start("owner",params(1000,128),baseline,0,1).at("watch_id").get<std::string>();
    baseline["objects"][0]["properties"][0]["value"]=999;
    require(model.read("owner",query(id)).at("events")[0].at("snapshot").at("objects")[0]
              .at("properties")[0].at("value")==1,"Baseline не является независимой копией");
    int calls=0;
    for(int at=100;at<=400;at+=100) {
        model.pump(at,at,[&](const json&) {
            ++calls;auto data=snapshot(1);
            data["objects"][0]["properties"][0]["value"]=std::string(100000,static_cast<char>('a'+calls));
            return data;
        });
        model.pump(at,at,[&](const json&){++calls;return snapshot(999);});
    }
    require(calls==4,"Sampling догонял пропущенные интервалы или прочёл дважды за один срок");
    const auto read=model.read("owner",query(id));
    require(read.at("state")=="running" && read.at("dropped_events").get<int>()>0,
            "Байтовый предел не удалил старые обычные события");
    require(read.at("buffer_bytes").get<std::size_t>()<=Observations::MaxBufferBytes,
            "Несколько обычных событий превысили предел буфера");
    require(read.at("gap")==true && read.at("oldest_seq").get<int>()>1,"Не сообщена байтовая потеря начала");
    model.disconnect("owner");
}

void scalar_argument_boundaries() {
    require(g1r_mcp::parameter_extent(8,8,1,1)==9,"AS bool return beyond ParmsSize не включён");
    require(g1r_mcp::parameter_extent(8,8,8,1)==16,"AS object return beyond ParmsSize не включён");
    require(g1r_mcp::parameter_extent(8,0,8,1)==8,"Обычный параметр изменил размер");
    error("unsupported_signature",[]{g1r_mcp::parameter_extent(8,4096,1,1);});
    error("unsupported_signature",[]{g1r_mcp::parameter_extent(8,-1,1,1);});
    error("unsupported_signature",[]{g1r_mcp::parameter_extent(8,0,1,2);});
    g1r_mcp::validate_bool_layout(1,0,1);
    g1r_mcp::validate_bool_layout(1,0,255);
    error("unsupported_property_layout",[]{g1r_mcp::validate_bool_layout(1,1,1);});
    error("unsupported_property_layout",[]{g1r_mcp::validate_bool_layout(0,0,1);});
    error("unsupported_property_layout",[]{g1r_mcp::validate_bool_layout(1,0,0);});
    require(g1r_mcp::int32_argument(json(std::numeric_limits<std::int32_t>::min()))==
            std::numeric_limits<std::int32_t>::min(),"INT32_MIN не принимается");
    require(g1r_mcp::int32_argument(json(std::numeric_limits<std::int32_t>::max()))==
            std::numeric_limits<std::int32_t>::max(),"INT32_MAX не принимается");
    require(g1r_mcp::int32_argument(json(std::uint64_t{0}))==0,"Unsigned zero не принимается");
    error("invalid_argument",[]{g1r_mcp::int32_argument(json(std::numeric_limits<std::uint64_t>::max()));});
    error("invalid_argument",[]{g1r_mcp::int32_argument(json(std::uint64_t{2147483648ULL}));});
    error("invalid_argument",[]{g1r_mcp::int32_argument(json(std::int64_t{-2147483649LL}));});
    error("invalid_argument",[]{g1r_mcp::int32_argument(json(1.0));});
    error("invalid_argument",[]{g1r_mcp::int32_argument(json(true));});
    error("invalid_argument",[]{g1r_mcp::float_argument(json(1e300));});
    error("invalid_argument",[]{g1r_mcp::float_argument(json(-1e300));});
    error("invalid_argument",[]{g1r_mcp::float_argument(json(std::numeric_limits<double>::quiet_NaN()));});
    error("invalid_argument",[]{g1r_mcp::double_argument(json(std::numeric_limits<double>::infinity()));});
    require(g1r_mcp::float_argument(json(std::numeric_limits<float>::max()))==
            std::numeric_limits<float>::max(),"float max не принимается");
    require(g1r_mcp::double_argument(json(1e300))==1e300,"Конечный double потерян");
}
void object_argument_boundaries_and_inheritance() {
    const g1r_mcp::ObjectIdentity identity{1,2,3,4};
    require(g1r_mcp::same_object_identity(identity,{1,2,3,4}),"Одинаковая идентичность не переиспользуется");
    require(!g1r_mcp::same_object_identity(identity,{1,9,3,4}),"Изменение класса не меняет идентичность");
    require(!g1r_mcp::same_object_identity(identity,{1,2,3,9}),"Изменение имени не меняет идентичность");
    require(!g1r_mcp::same_object_identity(identity,{9,2,3,4}),"Изменение указателя не меняет идентичность");
    require(g1r_mcp::object_argument_handle(json(nullptr)).empty(),"Null объектного аргумента не принят");
    require(g1r_mcp::object_argument_handle(json{{"handle","obj-1"}})=="obj-1","Handle не принят");
    error("invalid_object_argument",[]{g1r_mcp::object_argument_handle(json(42));});
    error("invalid_object_argument",[]{g1r_mcp::object_argument_handle(json{{"handle","obj-1"},{"ptr",42}});});
    error("invalid_object_argument",[]{g1r_mcp::object_argument_handle(json{{"handle",""}});});
    error("invalid_object_argument",[]{g1r_mcp::object_argument_handle(json{{"handle",std::string(81,'x')}});});
    auto parent=[](std::uintptr_t klass){return klass==3?std::uintptr_t{2}:klass==2?std::uintptr_t{1}:std::uintptr_t{0};};
    require(g1r_mcp::compatible_object_class(3,3,parent),"Точный класс не принят");
    require(g1r_mcp::compatible_object_class(3,1,parent),"Наследник не принят");
    require(!g1r_mcp::compatible_object_class(1,3,parent),"Базовый класс принят вместо наследника");
    error("invalid_object_class",[]{g1r_mcp::compatible_object_class(1,2,[](std::uintptr_t){return std::uintptr_t{1};});});
    error("invalid_object_class",[]{g1r_mcp::compatible_object_class(1,200,[](std::uintptr_t c){return c+1;});});
    error("invalid_object_class",[]{g1r_mcp::compatible_object_class(0,1,[](std::uintptr_t){return std::uintptr_t{0};});});
    Observations model;
    auto data=snapshot(1);
    data["objects"][0]["properties"][0]["type"]="ObjectProperty";
    data["objects"][0]["properties"][0]["value"]={{"handle","obj-2"},{"index",2},
        {"name","Peer"},{"path","World.Peer"},{"class","Actor"},{"session_id","owner"}};
    const auto id=model.start("owner",params(),data,0,1).at("watch_id").get<std::string>();
    model.pump(10,2,[&](const json&){return data;});
    require(model.read("owner",query(id)).at("events").size()==1,"Стабильный object descriptor дал ложное изменение");
    model.pump(20,3,[&](const json&){auto empty=data;empty["objects"][0]["properties"][0]["value"]=nullptr;return empty;});
    require(model.read("owner",query(id)).at("events").size()==2,"Изменение object descriptor в null не замечено");
}
}

int main() {
    try {
        changes_limits_and_stop();
        expiry_invalidation_and_disconnect();
        two_watches_and_byte_bounds();
        invalid_parameters_and_capacity();
        copied_values_byte_drop_and_no_catchup();
        scalar_argument_boundaries();
        object_argument_boundaries_and_inheritance();
        std::cout << "Проверки наблюдений пройдены\n";
        return 0;
    } catch (const std::exception& caught) {
        std::cerr << caught.what() << '\n';
        return 1;
    }
}
