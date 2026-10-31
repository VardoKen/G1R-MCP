#pragma once

#include <Windows.h>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <future>
#include <memory>
#include <map>
#include <mutex>
#include <string>
#include <unordered_map>
#include <nlohmann/json.hpp>
#include "g1r_mcp/observations.hpp"
#include "g1r_mcp/enum_values.hpp"
#include "g1r_mcp/object_serial.hpp"
#include "g1r_mcp/job_state.hpp"
#include "g1r_mcp/vector_layout.hpp"
#include "g1r_mcp/transform_layout.hpp"
#include "g1r_mcp/linear_color_layout.hpp"

namespace g1r_mcp {
class GameAccess {
public:
    bool start();
    nlohmann::json request(const nlohmann::json& envelope);
    void pump();
    nlohmann::json status() const;
    void disconnect(const std::string& session);

private:
    struct Job {
        nlohmann::json request;
        std::promise<nlohmann::json> result;
        JobState state;
    };
    struct Handle {
        void* object{};
        void* object_class{};
        int index{};
        uint64_t name{};
        int32_t serial{};
        int32_t class_index{};
        uint64_t class_name{};
        int32_t class_serial{};
    };
    struct SlotIdentity {void* object{};int32_t serial{};};
    static ObjectSerialIdentity serial_identity(const Handle& value);
    using TickFn = void(__fastcall*)(void*);
    using ProcessEventFn = void(__fastcall*)(void*, void*, void*);
    TickFn original_tick_{};
    ProcessEventFn process_event_{};
    uintptr_t base_{};
    std::atomic<bool> enabled_{false};
    std::atomic<DWORD> game_thread_{0};
    std::atomic<uint64_t> ticks_{0};
    std::atomic<uint64_t> last_tick_ms_{0};
    std::atomic<uint64_t> completed_jobs_{0};
    std::atomic<uint64_t> cancelled_jobs_{0};
    // Сохраняется до выгрузки процесса; новая MCP-сессия не снимает карантин.
    std::atomic<bool> string_inputs_quarantined_{false};
    std::atomic<std::size_t> string_inputs_retained_count_{0};
    std::atomic<std::size_t> string_inputs_retained_bytes_{0};
    std::atomic<uint32_t> string_inputs_quarantine_cause_{0};
    // Смена MCP-сессии не снимает карантин собственного буфера массива.
    std::atomic<bool> object_array_inputs_quarantined_{false};
    std::atomic<std::size_t> object_array_inputs_retained_count_{0};
    std::atomic<std::size_t> object_array_inputs_retained_bytes_{0};
    std::atomic<uint32_t> object_array_inputs_quarantine_cause_{0};
    std::string startup_error_;
    mutable std::mutex queue_mutex_;
    std::deque<std::shared_ptr<Job>> queue_;
    std::shared_ptr<Job> active_job_;
    std::unordered_map<std::string, Handle> handles_;
    std::map<std::string, nlohmann::json> signature_contracts_;
    uint64_t next_signature_token_{};
    uint64_t next_handle_{0};
    std::string handle_session_;
    bool session_cleanup_pending_{};
    Observations observations_;
    uint64_t next_snapshot_{};

    nlohmann::json execute(const nlohmann::json& request);
    std::string name(uint64_t fname) const;
    std::string object_name(void* object) const;
    std::string object_path(void* object) const;
    std::string handle_for(void* object, int index);
    Handle validate_object(void* object) const;
    void validate_class(void* object_class) const;
    bool is_compatible_object(void* object, void* expected_class) const;
    bool is_compatible_class(void* represented_class, void* property_class, void* meta_class) const;
    nlohmann::json object_descriptor(void* object, void* expected_class);
    nlohmann::json class_descriptor(void* object, void* property_class, void* meta_class);
    nlohmann::json property_value(uintptr_t address, const std::string& type, int size, uintptr_t field);
    nlohmann::json enum_property(uintptr_t address, const std::string& type, int size, uintptr_t field);
    nlohmann::json enum_metadata(void* object, const nlohmann::json& value, const std::string& underlying_type);
    nlohmann::json read_array(const nlohmann::json& params);
    nlohmann::json read_struct(void* object, const std::string& handle, const std::string& property_name);
    vector_layout::Snapshot capture_vector_parameter(std::uintptr_t field,int size,int dim) const;
    transform_layout::Snapshot capture_transform_parameter(std::uintptr_t field,int size,int dim) const;
    nlohmann::json capture_object_array_parameter(std::uintptr_t field,int size,int dim,std::uint64_t flags) const;
    linear_color_layout::Snapshot capture_linear_color_parameter(std::uintptr_t field,int size,int dim) const;
    struct EnumLayout {std::string underlying_type;int size;void* object;};
    struct EnumTable {Handle identity;std::string path;std::vector<g1r::enum_values::Entry> entries;};
    EnumLayout enum_layout(const std::string& type, int size, uintptr_t field) const;
    EnumTable enum_table(void* object) const;
    void* resolve(const std::string& id);
    void* slot(int index) const;
    SlotIdentity slot_identity(int index) const;
    nlohmann::json object_info(void* object, int index);
    nlohmann::json properties(void* object, int limit);
    nlohmann::json selected_properties(void* object, const nlohmann::json& names);
    nlohmann::json snapshot(const nlohmann::json& targets);
    nlohmann::json capture_function_contract(void* object, uintptr_t owner, uintptr_t function, const std::string& target_handle);
    nlohmann::json functions(void* object, int limit, const std::string& function_query, const std::string& target_handle);
    nlohmann::json invoke(void* object, const std::string& function_name, const nlohmann::json& arguments,
                          const std::string& target_handle, const nlohmann::json* expected_token);
    static void __fastcall hooked_tick(void* loop);
    static GameAccess* active_;
};
}
