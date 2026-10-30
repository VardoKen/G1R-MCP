#pragma once

#include <Windows.h>
#include <atomic>
#include <deque>
#include <future>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <nlohmann/json.hpp>
#include "g1r_mcp/observations.hpp"

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
        std::atomic<int> state{0};
    };
    struct Handle {
        void* object{};
        void* object_class{};
        int index{};
        uint64_t name{};
    };
    using TickFn = void(__fastcall*)(void*);
    using ProcessEventFn = void(__fastcall*)(void*, void*, void*);
    TickFn original_tick_{};
    ProcessEventFn process_event_{};
    uintptr_t base_{};
    std::atomic<bool> enabled_{false};
    std::atomic<DWORD> game_thread_{0};
    std::atomic<uint64_t> ticks_{0};
    std::string startup_error_;
    mutable std::mutex queue_mutex_;
    std::deque<std::shared_ptr<Job>> queue_;
    std::unordered_map<std::string, Handle> handles_;
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
    nlohmann::json object_descriptor(void* object, void* expected_class);
    nlohmann::json property_value(uintptr_t address, const std::string& type, int size, uintptr_t field);
    void* resolve(const std::string& id);
    void* slot(int index) const;
    nlohmann::json object_info(void* object, int index);
    nlohmann::json properties(void* object, int limit);
    nlohmann::json selected_properties(void* object, const nlohmann::json& names);
    nlohmann::json snapshot(const nlohmann::json& targets);
    nlohmann::json functions(void* object, int limit);
    nlohmann::json invoke(void* object, const std::string& function_name, const nlohmann::json& arguments);
    static void __fastcall hooked_tick(void* loop);
    static GameAccess* active_;
};
}
