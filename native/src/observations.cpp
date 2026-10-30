#include <g1r_mcp/observations.hpp>
#include <g1r_mcp/transport.hpp>
#include <algorithm>
#include <limits>
#include <set>
#include <utility>

namespace g1r_mcp {
namespace {
using Json = nlohmann::json;
std::uint64_t integer(const Json& params, const char* key, std::uint64_t minimum,
                      std::uint64_t maximum) {
    if (!params.contains(key) || !params.at(key).is_number_integer())
        throw HandlerError("invalid_watch_params", std::string("Требуется целое поле ") + key);
    const auto& value = params.at(key);
    if ((!value.is_number_unsigned() && value.get<std::int64_t>() < 0)
        || value.get<std::uint64_t>() < minimum || value.get<std::uint64_t>() > maximum)
        throw HandlerError("invalid_watch_params", std::string("Поле вне допустимого диапазона: ") + key);
    return value.get<std::uint64_t>();
}
std::string watch_id(const Json& params) {
    if (!params.contains("watch_id") || !params.at("watch_id").is_string()
        || params.at("watch_id").get_ref<const std::string&>().empty()
        || params.at("watch_id").get_ref<const std::string&>().size() > 80)
        throw HandlerError("invalid_watch_params", "Требуется корректный watch_id");
    return params.at("watch_id").get<std::string>();
}
bool complete_snapshot(const Json& snapshot) {
    if (!snapshot.is_object() || snapshot.value("partial", true)
        || snapshot.value("truncated", true) || !snapshot.contains("objects")) return false;
    return snapshot.at("objects").is_array() && !snapshot.at("objects").empty();
}
}

void Observations::validate_targets(const Json& targets) {
    if (!targets.is_array() || targets.empty() || targets.size() > 16)
        throw HandlerError("invalid_snapshot_params", "Снимок требует от 1 до 16 целей");
    std::set<std::string> handles;
    for (const auto& target : targets) {
        if (!target.is_object() || target.size()!=2 || !target.contains("handle") || !target.at("handle").is_string()
            || target.at("handle").get_ref<const std::string&>().empty()
            || target.at("handle").get_ref<const std::string&>().size() > 80
            || !target.contains("properties") || !target.at("properties").is_array()
            || target.at("properties").empty() || target.at("properties").size() > 32)
            throw HandlerError("invalid_snapshot_params", "Цель требует handle и от 1 до 32 имён свойств");
        if (!handles.insert(target.at("handle").get<std::string>()).second)
            throw HandlerError("invalid_snapshot_params", "Цели снимка не должны повторяться");
        std::set<std::string> properties;
        for (const auto& property : target.at("properties")) {
            if (!property.is_string() || property.get_ref<const std::string&>().empty()
                || property.get_ref<const std::string&>().size() > 256
                || !properties.insert(property.get<std::string>()).second)
                throw HandlerError("invalid_snapshot_params", "Имена свойств должны быть непустыми и уникальными");
        }
    }
}

Observations::Json Observations::metadata(const Watch& watch) {
    return {{"watch_id", watch.id}, {"session_id", watch.owner}, {"state", watch.state},
            {"reason", watch.reason}, {"cleanup_complete", watch.state != "running"},
            {"next_seq", watch.next_seq}, {"last_seq", watch.next_seq - 1},
            {"oldest_seq", watch.events.empty() ? watch.next_seq : watch.events.front().at("seq").get<std::uint64_t>()},
            {"dropped_events", watch.dropped}, {"capacity", watch.capacity},
            {"buffer_bytes", watch.bytes}, {"buffer_limit_bytes", MaxBufferBytes}};
}

void Observations::append(Watch& watch, Json event) {
    event["seq"] = watch.next_seq++;
    auto bytes = event.dump().size();
    if (bytes > MaxBufferBytes) {
        // Не оставляем неполную baseline/changed копию похожей на полный снимок.
        event.erase("snapshot");
        event["kind"] = "terminal";
        event["reason"] = "event_too_large";
        event["state"] = "failed";
        watch.state = "failed";
        watch.reason = "event_too_large";
        watch.latest = nullptr;
        bytes = event.dump().size();
    }
    while (!watch.events.empty() && (watch.events.size() >= watch.capacity
           || watch.bytes + bytes > MaxBufferBytes)) {
        watch.bytes -= watch.events.front().dump().size();
        watch.events.pop_front();
        ++watch.dropped;
    }
    watch.bytes += bytes;
    watch.events.push_back(std::move(event));
}

void Observations::finish(Watch& watch, const std::string& state,
                          const std::string& reason, std::uint64_t now_ms,
                          std::uint64_t tick, const Json* snapshot) {
    if (watch.state != "running") return;
    watch.state = state;
    watch.reason = reason;
    Json event{{"kind", "terminal"}, {"reason", reason}, {"state", state},
               {"elapsed_ms", now_ms - watch.started}, {"tick_count", tick}};
    if (snapshot) event["snapshot"] = *snapshot;
    append(watch, std::move(event));
    watch.latest = nullptr;
}

Observations::Json Observations::start(const std::string& owner, const Json& params,
                                      const Json& baseline, std::uint64_t now_ms,
                                      std::uint64_t tick) {
    if (owner.empty()) throw HandlerError("invalid_owner", "Владелец наблюдения отсутствует");
    if (!params.is_object() || !params.contains("handle") || !params.contains("properties"))
        throw HandlerError("invalid_watch_params", "Наблюдение требует handle и properties");
    Json targets = Json::array({{{"handle", params.at("handle")}, {"properties", params.at("properties")}}});
    validate_targets(targets);
    const auto duration = integer(params, "duration_ms", 1, 600000);
    const auto interval = integer(params, "interval_ms", 10, 60000);
    const auto capacity = integer(params, "capacity", 1, 128);
    if (watches_.size() >= MaxWatches)
        throw HandlerError("watch_limit", "Достигнут предел наблюдений; terminal запись можно явно удалить");
    if (!complete_snapshot(baseline))
        throw HandlerError("unsupported_watch_target", "Исходный снимок должен содержать полностью поддержанные значения");
    if (baseline.dump().size() > MaxSnapshotBytes - 4096)
        throw HandlerError("unsupported_watch_target", "Исходный снимок превышает предел наблюдения");
    Watch watch;
    watch.id = "watch-" + std::to_string(++next_id_);
    watch.owner = owner;
    watch.targets = std::move(targets);
    watch.latest = baseline.at("objects");
    watch.started = now_ms;
    watch.deadline = now_ms + duration;
    watch.interval = interval;
    watch.next_sample = now_ms + interval;
    watch.capacity = static_cast<std::size_t>(capacity);
    append(watch, {{"kind", "baseline"}, {"elapsed_ms", 0}, {"tick_count", tick}, {"snapshot", baseline}});
    auto result = metadata(watch);
    result["start_seq"] = 1;
    result["duration_ms"] = duration;
    result["interval_ms"] = interval;
    result["sampling"] = "at_most_one_due_watch_per_tick_no_catchup";
    order_.push_back(watch.id);
    watches_.emplace(watch.id, std::move(watch));
    return result;
}

const Observations::Watch& Observations::find(const std::string& owner, const Json& params) const {
    auto found = watches_.find(watch_id(params));
    if (found == watches_.end()) throw HandlerError("unknown_watch", "Наблюдение отсутствует в текущей среде");
    if (found->second.owner != owner) throw HandlerError("foreign_watch", "Наблюдение принадлежит другой сессии");
    return found->second;
}
Observations::Watch& Observations::find(const std::string& owner, const Json& params) {
    return const_cast<Watch&>(std::as_const(*this).find(owner, params));
}

Observations::Json Observations::read(const std::string& owner, const Json& params) const {
    const auto& watch = find(owner, params);
    const auto after = integer(params, "after_seq", 0, watch.next_seq - 1);
    const auto limit = integer(params, "limit", 1, 100);
    auto result = metadata(watch);
    result["events"] = Json::array();
    result["gap"] = !watch.events.empty() && after + 1 < watch.events.front().at("seq").get<std::uint64_t>();
    result["cursor"] = after;
    result["truncated"] = false;
    std::size_t bytes = result.dump().size();
    for (const auto& event : watch.events) {
        if (event.at("seq").get<std::uint64_t>() <= after) continue;
        const auto event_bytes = event.dump().size();
        if (result["events"].size() >= limit || bytes + event_bytes > MaxBufferBytes + 4096) {
            result["truncated"] = true;
            break;
        }
        bytes += event_bytes;
        result["events"].push_back(event);
        result["cursor"] = event.at("seq");
    }
    return result;
}

Observations::Json Observations::stop(const std::string& owner, const Json& params,
                                     std::uint64_t now_ms, std::uint64_t tick) {
    auto& watch = find(owner, params);
    finish(watch, "stopped", "explicit_stop", now_ms, tick);
    return metadata(watch);
}
Observations::Json Observations::forget(const std::string& owner, const Json& params) {
    const auto& watch = find(owner, params);
    if (watch.state == "running") throw HandlerError("watch_running", "Сначала остановить наблюдение");
    const auto id = watch.id;
    watches_.erase(id);
    order_.erase(std::remove(order_.begin(), order_.end(), id), order_.end());
    cursor_ = 0;
    return {{"watch_id", id}, {"forgotten", true}, {"cleanup_complete", true}};
}
void Observations::disconnect(const std::string& owner) {
    for (auto item = watches_.begin(); item != watches_.end();) {
        if (item->second.owner == owner) item = watches_.erase(item); else ++item;
    }
    order_.erase(std::remove_if(order_.begin(), order_.end(), [&](const auto& id) { return !watches_.contains(id); }), order_.end());
    cursor_ = 0;
}

void Observations::pump(std::uint64_t now_ms, std::uint64_t tick, const Reader& reader) {
    if (order_.empty()) return;
    for (std::size_t checked = 0; checked < order_.size(); ++checked) {
        cursor_ %= order_.size();
        auto& watch = watches_.at(order_.at(cursor_++));
        if (watch.state != "running") continue;
        if (now_ms >= watch.deadline) {
            finish(watch, "completed", "duration_elapsed", now_ms, tick);
            return;
        }
        if (now_ms < watch.next_sample) continue;
        watch.next_sample = now_ms + watch.interval;
        try {
            auto snapshot = reader(watch.targets);
            if (!complete_snapshot(snapshot)) {
                finish(watch, "failed", "object_or_property_invalidated", now_ms, tick, &snapshot);
            } else if (snapshot.at("objects") != watch.latest) {
                watch.latest = snapshot.at("objects");
                append(watch, {{"kind", "changed"}, {"elapsed_ms", now_ms - watch.started},
                               {"tick_count", tick}, {"snapshot", std::move(snapshot)}});
            }
        } catch (const HandlerError& error) {
            finish(watch, "failed", error.code(), now_ms, tick);
        } catch (...) {
            finish(watch, "failed", "snapshot_read_failed", now_ms, tick);
        }
        return;
    }
}

} // namespace g1r_mcp
