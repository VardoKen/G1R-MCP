#pragma once

#include <g1r_mcp/transport.hpp>
#include <cstdint>

namespace g1r_mcp::vector_parameter_direction {

// Значения EPropertyFlags подтверждены таблицей JMapGenerator.cpp UE4SS
// b4cefa18ac7349c4389c1ce244c1a70b66a05fe5: строки 564, 570–574, 588.
// 0x800 означает DisableEditOnTemplate, а ReferenceParm — 0x08000000.
inline constexpr std::uint64_t const_parm = 0x2;
inline constexpr std::uint64_t parm = 0x80;
inline constexpr std::uint64_t out_parm = 0x100;
inline constexpr std::uint64_t return_parm = 0x400;
inline constexpr std::uint64_t reference_parm = 0x08000000;

enum class Direction { input, output };

// Только FVector по значению или чистый FVector out. Const-ref/inout пока
// запрещены: соответствующий AngelScript-контракт ещё не проверен в игре.
inline Direction classify(std::uint64_t flags) {
    if(!(flags & parm) || (flags & (return_parm | const_parm | reference_parm)))
        throw HandlerError("unsupported_signature", "FVector параметр требует value input или pure out без Const/Reference/Return");
    return flags & out_parm ? Direction::output : Direction::input;
}

inline void validate_output_marker(const nlohmann::json& value) {
    if(!value.is_object() || value.size()!=1 || !value.contains("out")
       || !value.at("out").is_boolean() || !value.at("out").get<bool>())
        throw HandlerError("invalid_argument", "FVector out требует точный маркер {out:true}");
}

} // namespace g1r_mcp::vector_parameter_direction
