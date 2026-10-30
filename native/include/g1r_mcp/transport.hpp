#pragma once

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <winsock2.h>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <functional>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>

#include <nlohmann/json.hpp>

namespace g1r_mcp {

inline constexpr std::uint16_t DefaultPort = 27123;
inline constexpr std::uint32_t MaxFrameBytes = 1024U * 1024U;
inline constexpr std::size_t MaxSessionRequests = 1024;

class HandlerError : public std::runtime_error {
public:
    HandlerError(std::string code, std::string message)
        : std::runtime_error(std::move(message)), code_(std::move(code)) {}
    const std::string& code() const noexcept { return code_; }

private:
    std::string code_;
};

// Обработчик исполняется на рабочем потоке транспорта: игровые вызовы здесь
// запрещены. Вызывающий код передаёт работу в игровую очередь и ограничивает
// ожидание результата. stop() ждёт завершения уже вошедшего обработчика.
class Transport {
public:
    using Handler = std::function<nlohmann::json(const nlohmann::json&)>;
    using SessionClosed = std::function<void(const std::string&)>;

    explicit Transport(Handler handler, std::uint16_t port = DefaultPort,
                       std::chrono::milliseconds io_timeout = std::chrono::milliseconds{5000},
                       SessionClosed session_closed = {});
    ~Transport();
    Transport(const Transport&) = delete;
    Transport& operator=(const Transport&) = delete;

    bool start();
    void stop();
    nlohmann::json status() const;

private:
    void run();
    void serve(SOCKET client, const std::string& session);
    bool read_exact(SOCKET socket, char* bytes, std::size_t length,
                    std::chrono::steady_clock::time_point deadline);
    bool write_exact(SOCKET socket, const char* bytes, std::size_t length,
                     std::chrono::steady_clock::time_point deadline);
    bool wait_socket(SOCKET socket, bool writing,
                     std::chrono::steady_clock::time_point deadline);
    bool send_json(SOCKET client, const nlohmann::json& response);
    void set_error(std::string code, std::string message);
    std::string new_session();

    Handler handler_;
    SessionClosed session_closed_;
    std::uint16_t requested_port_;
    std::chrono::milliseconds io_timeout_;
    mutable std::mutex state_mutex_;
    std::mutex lifecycle_mutex_;
    SOCKET listener_{INVALID_SOCKET};
    SOCKET active_client_{INVALID_SOCKET};
    std::uint16_t bound_port_{0};
    bool winsock_started_{false};
    std::string session_;
    std::string last_error_code_;
    std::string last_error_message_;
    std::uint64_t session_counter_{0};
    std::atomic_bool stop_requested_{true};
    std::atomic_bool running_{false};
    std::thread worker_;
};

} // namespace g1r_mcp
