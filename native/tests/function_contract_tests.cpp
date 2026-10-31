#include <g1r_mcp/function_contract.hpp>
#include <iostream>
#include <stdexcept>
#include <vector>
namespace {
int checks{};
void require(bool good,const char* message){++checks;if(!good)throw std::runtime_error(message);}
template<class Action>void reject(const char* code,Action&& action) {
    try {action();}catch(const g1r_mcp::HandlerError& error) {
        require(error.code()==code,"Неверный код отказа сигнатуры");
        require(error.details()==nlohmann::json{{"executed",false},{"may_have_executed",false},{"do_not_retry",false}},"Отказ должен быть до исполнения");return;
    }
    throw std::runtime_error("Ожидался отказ сигнатуры");
}
}
int main() {
    using namespace g1r_mcp;using nlohmann::json;
    try {
        std::map<std::string,json> saved;std::uint64_t sequence{};
        const json base={{"session","native-1"},{"target_handle","obj-1"},{"function_name","ReadMarker"},
            {"target",{{"serial",0},{"class_serial",0},{"name",9}}},
            {"function",{{"pointer",123},{"serial",0},{"class_serial",0},{"flags",1}}},
            {"owner",{{"pointer",456},{"serial",0},{"class_serial",0}}},
            {"header",{{"parameter_count",1},{"parameter_size",4},{"return_offset",0},{"child_head",789}}},
            {"fields",json::array({{{"pointer",789},{"next",std::uint64_t{0}},{"type","IntProperty"},
                                   {"flags",std::uint64_t{0x580}},{"name",12},{"offset",0},{"size",4},{"array_dim",1}}})}};
        const auto first=store_function_contract(saved,sequence,base);
        require(first=="sig-1","Токен последовательности неверен");
        require(store_function_contract(saved,sequence,base)==first && sequence==1 && saved.size()==1,"Опрос не должен исчерпывать токены");
        require(checked_function_contract(saved,first,"native-1","obj-1","ReadMarker")==base,"Токен не вернул полную запись");
        for(const json& invalid:std::vector<json>{nullptr,true,7,"","sig-","sig-0","sig-01","sig-+1","sig--1","SIG-1","sig-1x",std::string("sig-1\0x",7),"sig-123456789012345678901"})
            reject("invalid_signature_token",[&]{checked_function_contract(saved,invalid,"native-1","obj-1","ReadMarker");});
        reject("unknown_signature_token",[&]{checked_function_contract(saved,"sig-2","native-1","obj-1","ReadMarker");});
        for(const auto& input:std::vector<std::vector<std::string>>{{"native-2","obj-1","ReadMarker"},{"native-1","obj-2","ReadMarker"},{"native-1","obj-1","Other"}})
            reject("signature_token_mismatch",[&]{checked_function_contract(saved,first,input[0],input[1],input[2]);});
        require_same_function_contract(base,base);
        for(const char* path:{"/target/serial","/target/class_serial","/function/pointer","/function/serial","/function/flags","/owner/pointer","/owner/serial","/header/parameter_count","/header/parameter_size","/header/return_offset","/header/child_head","/fields/0/pointer","/fields/0/next","/fields/0/flags","/fields/0/name","/fields/0/offset","/fields/0/size","/fields/0/array_dim"}) {
            auto changed=base;auto& value=changed[json::json_pointer(path)];value=value.get<std::uint64_t>()+1;
            reject("signature_changed",[&]{require_same_function_contract(base,changed);});
        }
        auto type_changed=base;type_changed["fields"][0]["type"]="FloatProperty";
        reject("signature_changed",[&]{require_same_function_contract(base,type_changed);});
        auto auxiliary=base;auxiliary["fields"][0]["aux"]={{"mask",1},{"class",55},{"enum_names",json::array({{{"name",1},{"value",2}}})}};
        for(const char* path:{"/fields/0/aux/mask","/fields/0/aux/class","/fields/0/aux/enum_names/0/value"}) {
            auto changed=auxiliary;changed[json::json_pointer(path)]=3;
            reject("signature_changed",[&]{require_same_function_contract(auxiliary,changed);});
        }
        saved.clear();auto new_session=base;new_session["session"]="native-2";
        require(store_function_contract(saved,sequence,new_session)=="sig-2","Токены нельзя повторять после disconnect");
        reject("unknown_signature_token",[&]{checked_function_contract(saved,first,"native-2","obj-1","ReadMarker");});
        for(int i=0;i<255;++i){auto entry=base;entry["function_name"]="Other"+std::to_string(i);store_function_contract(saved,sequence,entry);}
        auto extra=base;extra["function_name"]="Overflow";
        reject("signature_contract_limit",[&]{store_function_contract(saved,sequence,extra);});
        require(saved.size()==256,"Лимит не должен удалять существующие токены");
        auto oversized=base;oversized["message"]=std::string(MaxFunctionContractBytes,'x');
        reject("signature_contract_too_large",[&]{store_function_contract(saved,sequence,oversized);});
        const auto chain=capture_function_field_chain(1,[](std::uintptr_t pointer){return json{{"pointer",pointer},{"next",pointer<128?pointer+1:std::uintptr_t{0}},{"flags",pointer==1?0:0x80}};});
        require(chain.size()==128 && chain[0]["flags"]==0,"128 полей включая не-Parm должны сохраняться");
        reject("unsupported_signature",[&]{capture_function_field_chain(1,[](std::uintptr_t pointer){return json{{"next",pointer+1}};});});
        reject("unsupported_signature",[&]{capture_function_field_chain(1,[](std::uintptr_t){return json{{"next",std::uintptr_t{1}}};});});
        require(capture_function_field_chain(0,[](std::uintptr_t){return json{};}).empty(),"Пустая цепь допустима");
        std::cout<<"function_contract: "<<checks<<" checks passed\n";return 0;
    }catch(const std::exception& error){std::cerr<<error.what()<<"\n";return 1;}
}
