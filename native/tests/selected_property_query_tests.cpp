#include <g1r_mcp/selected_property_query.hpp>

#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
int checks=0;
void require(bool condition,const char* message) {
    ++checks;
    if(!condition)throw std::runtime_error(message);
}
template<class Operation>
void rejects(Operation operation) {
    try {operation();}
    catch(const g1r_mcp::HandlerError& error) {
        require(error.code()=="invalid_property_names","Неверный код отказа списка свойств");
        require(error.details()==nlohmann::json{{"executed",false},{"may_have_executed",false},{"do_not_retry",false}},
                "Неверный список должен отвергаться до исполнения");
        return;
    }
    throw std::runtime_error("Ожидался отказ списка свойств");
}
}

int main() {
    using nlohmann::json;
    namespace query=g1r_mcp::selected_property_query;
    try {
        const json requested=json::array({"StoredNone","StoredEntry","MissingForProbe","CallsCount"});
        int calls=0;
        const auto result=query::validated(requested,[&](const json& names) {
            ++calls;
            require(&names==&requested,"Reader должен получать исходный запрос без копии/нормализации");
            return names;
        });
        require(calls==1 && result==requested,"Порядок имён не должен меняться");
        const json differently_cased=json::array({"Entry","entry"," Entry "});
        query::validate(differently_cased);
        require(differently_cased[0]=="Entry" && differently_cased[1]=="entry" && differently_cased[2]==" Entry ",
                "Проверка не должна сворачивать регистр или убирать пробелы");

        json maximum=json::array();
        for(std::size_t i=0;i<query::max_properties;++i)maximum.push_back("Property"+std::to_string(i));
        query::validate(maximum);
        require(maximum.size()==32,"Граница 32 свойства разрешена");
        query::validate(json::array({std::string(256,'a')}));
        std::string unicode;
        for(int i=0;i<128;++i)unicode+="\xD0\xAF";
        query::validate(json::array({unicode}));
        require(unicode.size()==256,"Предел имени проверяется в байтах UTF-8");

        auto too_many=maximum;
        too_many.push_back("Property32");
        const std::vector<json> invalid={
            nullptr,false,7,"StoredEntry",json::object(),json::array(),too_many,
            json::array({nullptr}),json::array({false}),json::array({1}),json::array({json::object()}),
            json::array({""}),json::array({std::string(257,'a')}),json::array({unicode+"a"}),
            json::array({std::string("Entry\0Other",11)}),json::array({"Entry","Entry"}),
            json::array({"StoredEntry",nullptr})};
        for(const auto& names:invalid) {
            calls=0;
            rejects([&]{query::validated(names,[&](const json&){++calls;return true;});});
            require(calls==0,"Неверный список не должен доходить до reader");
        }
        std::cout<<"selected_property_query: "<<checks<<" checks passed\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr<<error.what()<<'\n';
        return 1;
    }
}
