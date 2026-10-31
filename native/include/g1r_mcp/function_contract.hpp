#pragma once
#include <g1r_mcp/transport.hpp>
#include <algorithm>
#include <cstdint>
#include <functional>
#include <limits>
#include <map>
#include <set>
#include <string>
#include <utility>

namespace g1r_mcp {
inline constexpr std::size_t MaxFunctionContracts=256;
inline constexpr std::size_t MaxFunctionContractBytes=65536;
inline bool valid_signature_token(const std::string& value) noexcept {
    if(value.size()<5 || value.size()>24 || value.compare(0,4,"sig-") || value[4]=='0')return false;
    return std::all_of(value.begin()+4,value.end(),[](unsigned char c){return c>='0' && c<='9';});
}
inline const nlohmann::json& checked_function_contract(const std::map<std::string,nlohmann::json>& saved,
                                                     const nlohmann::json& token,
                                                     const std::string& session,const std::string& handle,
                                                     const std::string& function_name) {
    if(!token.is_string() || !valid_signature_token(token.get_ref<const std::string&>()))
        throw HandlerError("invalid_signature_token","Требуется выданный токен sig-N");
    const auto found=saved.find(token.get<std::string>());
    if(found==saved.end())throw HandlerError("unknown_signature_token","Токен отсутствует в текущей native-сессии; перечитайте сигнатуру");
    const auto& contract=found->second;
    if(contract.at("session")!=session || contract.at("target_handle")!=handle || contract.at("function_name")!=function_name)
        throw HandlerError("signature_token_mismatch","Токен выдан другой сессии, объекту или функции");
    return contract;
}
inline void require_same_function_contract(const nlohmann::json& expected,const nlohmann::json& actual) {
    if(expected!=actual)throw HandlerError("signature_changed","Отражённая сигнатура изменилась после чтения; вызов не выполнен");
}
inline std::string store_function_contract(std::map<std::string,nlohmann::json>& saved,std::uint64_t& sequence,
                                         const nlohmann::json& contract) {
    if(contract.dump().size()>MaxFunctionContractBytes)
        throw HandlerError("signature_contract_too_large","Контракт функции превышает 64 КиБ");
    for(const auto& [token,known]:saved)if(known==contract)return token;
    if(saved.size()>=MaxFunctionContracts)
        throw HandlerError("signature_contract_limit","Сохранено 256 сигнатур; существующие токены сохранены");
    if(sequence==std::numeric_limits<std::uint64_t>::max())
        throw HandlerError("signature_contract_limit","Счётчик токенов исчерпан");
    const auto token="sig-"+std::to_string(++sequence);
    saved.emplace(token,contract);return token;
}
// Включаем все поля цепочки: CPF_Parm может измениться между двумя чтениями.
template<class Reader>nlohmann::json capture_function_field_chain(std::uintptr_t head,Reader&& read_field) {
    nlohmann::json fields=nlohmann::json::array();std::set<std::uintptr_t> visited;
    while(head) {
        if(visited.size()>=128 || !visited.insert(head).second)
            throw HandlerError("unsupported_signature","Цепочка параметров циклическая или превышает 128 полей");
        auto field=std::invoke(std::forward<Reader>(read_field),head);
        if(!field.is_object() || !field.contains("next") || !field.at("next").is_number_unsigned())
            throw HandlerError("unsupported_signature","Неполная запись поля сигнатуры");
        fields.push_back(std::move(field));head=fields.back().at("next").get<std::uintptr_t>();
    }
    return fields;
}
}
