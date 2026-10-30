#pragma once

#include <g1r_mcp/transport.hpp>
#include <cstdint>
#include <exception>
#include <functional>
#include <limits>
#include <string>
#include <utility>

namespace g1r_mcp {
// Скопированный layout FString/TArray<TCHAR> для текущей x64-сборки.
// Проверка не утверждает принадлежность памяти allocator: её устанавливает
// контракт успешно выполненного StrProperty-возврата ProcessEvent.
struct StringHeader {
    std::uintptr_t data;
    std::int32_t count;
    std::int32_t capacity;
};
static_assert(sizeof(StringHeader)==16);

inline void validate_string_header(const StringHeader& header) {
    if(header.count<0 || header.capacity<0 || header.count>header.capacity)
        throw HandlerError("invalid_string","Некорректные размеры FString");
    if(!header.data) {
        if(header.count || header.capacity)
            throw HandlerError("invalid_string","FString без указателя содержит размер или ёмкость");
        return;
    }
    if(!header.capacity || header.data%alignof(char16_t))
        throw HandlerError("invalid_string","Некорректный указатель или ёмкость FString");
    const auto bytes=static_cast<std::uintptr_t>(header.capacity)*sizeof(char16_t);
    if(header.data>std::numeric_limits<std::uintptr_t>::max()-bytes)
        throw HandlerError("invalid_string","Адресный диапазон FString переполняется");
}

inline nlohmann::json string_cleanup_details(bool attempted,bool confirmed,
                                             const std::string& cause) {
    return {{"executed",true},{"may_have_executed",true},{"do_not_retry",true},
            {"cause_code",cause},{"cleanup_attempted",attempted},
            {"cleanup_confirmed",confirmed},{"cleanup_state",confirmed?"released":"unknown"}};
}

// Decoder только читает header; Releaser вызывается для валидного ненулевого
// указателя ровно один раз. После ошибки освобождения повтор запрещён.
template<class Decoder,class Releaser>
auto copied_owned_string_return(const StringHeader& header,Decoder&& decoder,Releaser&& releaser) {
    try {validate_string_header(header);}
    catch(const HandlerError& error) {
        throw HandlerError("game_result_error","Вызов выполнен, но FString имеет неверный layout; очистка не подтверждена",
                           string_cleanup_details(false,false,error.code()));
    }
    using Result=std::invoke_result_t<Decoder,const StringHeader&>;
    Result result{};
    std::exception_ptr decoding_error;
    std::string cause="result_processing_failed";
    try {result=std::invoke(std::forward<Decoder>(decoder),header);}
    catch(const HandlerError& error) {cause=error.code();decoding_error=std::current_exception();}
    catch(...) {decoding_error=std::current_exception();}
    bool cleanup_confirmed=true;
    if(header.data) {
        try {cleanup_confirmed=std::invoke(std::forward<Releaser>(releaser),header.data);}
        catch(...) {cleanup_confirmed=false;}
    }
    if(!cleanup_confirmed) {
        auto details=string_cleanup_details(true,false,"string_cleanup_fault");
        if(decoding_error)details["result_cause_code"]=cause;
        throw HandlerError("game_result_error","Вызов выполнен; освобождение FString не подтверждено, повтор запрещён",
                           std::move(details));
    }
    if(decoding_error)
        throw HandlerError("game_result_error","Вызов выполнен, FString освобождён, но результат не прочитан; не повторять запрос",
                           string_cleanup_details(header.data!=0,true,cause));
    return result;
}
} // namespace g1r_mcp
