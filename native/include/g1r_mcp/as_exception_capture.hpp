#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>
#include <utility>
#include <vector>
#include <nlohmann/json.hpp>

namespace g1r_mcp::as_exception_capture {

struct State {
    std::size_t exception_count{};
    std::vector<std::string> messages;
    bool full_capture{true};
};

inline thread_local State* active{};

// Область существует только вокруг текущего ProcessEvent. Вложенный вызов
// временно принадлежит своей записи, затем возвращает прежнего владельца.
class Scope {
public:
    explicit Scope(State& state) noexcept:previous_(std::exchange(active,&state)) {}
    ~Scope() {active=previous_;}
    Scope(const Scope&)=delete;
    Scope& operator=(const Scope&)=delete;
private:
    State* previous_;
};

// Reader копирует один байт через существующий SEH-reader. Ни указатель logger,
// ни частично прочитанное сообщение не сохраняются; исходный logger вызывается
// вызывающей стороной даже при ошибке копирования.
template<class Reader>void record(const char* message,Reader&& read) noexcept {
    auto* state=active;
    if(!state)return;
    ++state->exception_count;
    try {
        std::string copied;
        if(!message)copied="NO EXCEPTION";
        else {
            auto address=reinterpret_cast<std::uintptr_t>(message);
            for(;;++address) {
                char value{};
                if(!std::invoke(read,address,value)) {
                    state->full_capture=false;
                    return;
                }
                if(!value)break;
                copied.push_back(value);
            }
        }
        state->messages.push_back(std::move(copied));
    } catch(...) {state->full_capture=false;}
}

inline nlohmann::json describe(const State& state) {
    nlohmann::json result={{"source","observed_logging_entry"},{"exception_count",state.exception_count},
        {"full_capture",state.full_capture},{"stack_captured",false},{"messages",nlohmann::json::array()}};
    nlohmann::json message_bytes=nlohmann::json::array();
    for(std::size_t index=0;index<state.messages.size();++index) {
        const auto& message=state.messages[index];
        const nlohmann::json text=message;
        try {
            static_cast<void>(text.dump());
            result["messages"].push_back(text);
        } catch(const nlohmann::json::type_error&) {
            result["messages"].push_back(nullptr);
            std::string hexadecimal;
            constexpr char digits[]="0123456789abcdef";
            for(const unsigned char byte:message) {
                hexadecimal.push_back(digits[byte>>4]);
                hexadecimal.push_back(digits[byte&15]);
            }
            message_bytes.push_back({{"index",index},{"encoding","hex"},{"bytes",std::move(hexadecimal)}});
        }
    }
    if(!message_bytes.empty())result["message_bytes"]=std::move(message_bytes);
    return result;
}

} // namespace g1r_mcp::as_exception_capture
