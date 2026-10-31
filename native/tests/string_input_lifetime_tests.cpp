#include <g1r_mcp/string_input_lifetime.hpp>

#include <iostream>
#include <limits>
#include <stdexcept>
#include <type_traits>
#include <vector>

namespace {
using nlohmann::json;
using g1r_mcp::StringHeader;
using g1r_mcp::string_input_lifetime::Ledger;
int checks{};
void require(bool good,const char* message) {++checks;if(!good)throw std::runtime_error(message);}
template<class Action>void reject(const char* code,Action&& action) {
    try {action();}
    catch(const g1r_mcp::HandlerError& error) {
        require(error.code()==code,"Неверный отказ учёта FString");
        require(error.details()==json{{"executed",false},{"may_have_executed",false},{"do_not_retry",false}},
                "Чистый helper должен сообщать ранний отказ; эффект вызова учитывается caller отдельно");return;
    }
    throw std::runtime_error("Ожидался отказ учёта FString");
}
void no_free(Ledger& ledger) {
    int calls{};const auto result=ledger.release([&](std::uintptr_t){++calls;return true;});
    require(!result.attempted && !result.confirmed && result.uncertain && calls==0,
            "Неопределённое владение должно запрещать Free");
}
}

int main() {
    using namespace g1r_mcp;
    using namespace string_input_lifetime;
    static_assert(!std::is_copy_constructible_v<Ledger> && !std::is_copy_assignable_v<Ledger>);
    static_assert(!std::is_move_constructible_v<Ledger> && !std::is_move_assignable_v<Ledger>);
    try {
        {InputBudget budget;for(int i=0;i<32;++i)budget.add(0);
            require(budget.input_count()==32 && budget.allocation_bytes()==64,"Пустые inputs учитывают 32 терминатора в предварительном бюджете");
            reject("string_budget_exceeded",[&]{budget.add(0);});
            require(budget.input_count()==32 && budget.allocation_bytes()==64,"Отказ количества должен сохранять бюджет");}
        {InputBudget budget;for(int i=0;i<4;++i)budget.add(string_arguments::max_utf16_units);
            require(budget.input_count()==4 && budget.allocation_bytes()==max_input_bytes,"Четыре максимальные строки равны 256 КиБ с терминаторами");
            reject("string_budget_exceeded",[&]{budget.add(0);});
            require(budget.input_count()==4 && budget.allocation_bytes()==max_input_bytes,"Отказ байтов должен сохранять бюджет");
            reject("string_budget_exceeded",[&]{budget.add(std::numeric_limits<std::size_t>::max());});}

        {Ledger ledger;ledger.add_input({0,0,0},0);ledger.add_return({0,0,0});
            require(ledger.input_count()==1 && ledger.allocation_count()==0 && ledger.allocation_bytes()==0,"Null empty не владеет allocation");
            int calls{};const auto released=ledger.release([&](std::uintptr_t){++calls;return true;});
            require(!released.attempted && released.confirmed && !released.uncertain && calls==0,"Пустой ledger очищается без allocator");}
        {Ledger ledger;ledger.add_input({0x1000,4,8},3);ledger.add_input({0x1010,2,2},1);
            ledger.validate_unchanged_input(0,{0x1000,4,8});ledger.validate_unchanged_input(1,{0x1010,2,2});
            require(ledger.allocation_count()==2 && ledger.allocation_bytes()==20,"Смежные allocations должны быть допустимы и учитывать capacity");
            std::vector<std::uintptr_t> calls;const auto result=ledger.release([&](std::uintptr_t pointer){calls.push_back(pointer);return true;});
            require(calls==std::vector<std::uintptr_t>{0x1000,0x1010} && result.released_count==2 && result.confirmed,"Каждая собственная allocation освобождается один раз");
            require(ledger.retained_count()==0 && ledger.retained_bytes()==0,"Успешная очистка не сохраняет allocation");
            const auto again=ledger.release([&](std::uintptr_t pointer){calls.push_back(pointer);return true;});
            require(calls.size()==2 && !again.attempted && again.confirmed && again.released_count==2,"Повторная cleanup не должна повторять Free");}
        {Ledger ledger;ledger.add_input({0x1000,4,8},3);ledger.add_return({0x1000,2,8});
            require(ledger.allocation_count()==1 && ledger.allocation_bytes()==16,"Exact pointer/capacity return alias должен дедуплицироваться при иной count");
            int calls{};auto released=ledger.release([&](std::uintptr_t pointer){++calls;require(pointer==0x1000,"Освобождён другой алиас");return true;});
            require(calls==1 && released.released_count==1 && released.confirmed,"Return alias не должен давать double Free");}
        {Ledger ledger;ledger.add_input({0x1000,4,8},3);ledger.add_return({0x2000,0,8});
            require(ledger.allocation_count()==2 && ledger.allocation_bytes()==32,"Reserved empty return владеет отдельной allocation");
            int calls{};const auto released=ledger.release([&](std::uintptr_t){++calls;return true;});
            require(calls==2 && released.confirmed,"Reserved empty return должен освобождаться вместе с input");}

        for(const auto alias:std::vector<StringHeader>{{0x1002,3,7},{0x1000,4,7},{0x1000,4,9},{0x0ffe,2,2}}) {
            Ledger ledger;ledger.add_input({0x1000,4,8},3);
            reject("string_lifetime_unknown",[&]{ledger.add_return(alias);});
            require(ledger.uncertain() && ledger.retained_count()==1 && ledger.retained_bytes()==16,"Частичный алиас должен сохранить известную allocation в карантине");
            no_free(ledger);
        }
        {Ledger ledger;ledger.add_input({0x1000,4,8},3);
            reject("string_lifetime_unknown",[&]{ledger.add_input({0x1000,4,8},3);});no_free(ledger);}
        for(const auto header:std::vector<StringHeader>{{0x1000,3,8},{0x1000,4,7},{0x2000,4,8}}) {
            Ledger ledger;ledger.add_input({0x1000,4,8},3);
            reject("string_lifetime_unknown",[&]{ledger.validate_unchanged_input(0,header);});no_free(ledger);
        }
        {Ledger ledger;ledger.add_input({0x1000,4,8},3);
            reject("string_lifetime_unknown",[&]{ledger.validate_unchanged_input(1,{0x1000,4,8});});no_free(ledger);}
        {Ledger ledger;ledger.add_input({0x1000,4,8},3);ledger.mark_uncertain();
            no_free(ledger);reject("string_lifetime_unknown",[&]{ledger.add_return({0x2000,2,2});});}
        {Ledger ledger;ledger.mark_uncertain();no_free(ledger);
            require(ledger.retained_count()==0,"Неизвестный constructor не должен создавать выдуманную запись allocation");}
        {Ledger ledger;reject("string_lifetime_unknown",[&]{ledger.add_input({0x1000,2,2},3);});no_free(ledger);}
        {Ledger ledger;reject("string_lifetime_unknown",[&]{ledger.add_input({0x1000,0,8},0);});no_free(ledger);}
        for(const auto invalid:std::vector<StringHeader>{{0,1,1},{0x1001,2,2},{0x1000,-1,2},{0x1000,3,2},
                {std::numeric_limits<std::uintptr_t>::max()-1,2,2}}) {
            Ledger ledger;reject("invalid_string",[&]{ledger.add_input(invalid,1);});no_free(ledger);
        }
        {Ledger ledger;ledger.add_input({0x1000,2,2},1);
            reject("invalid_string",[&]{ledger.add_return({std::numeric_limits<std::uintptr_t>::max()-1,2,2});});
            require(ledger.retained_count()==1 && ledger.retained_bytes()==4,"Return range overflow сохраняет известный input в карантине");no_free(ledger);}
        {Ledger ledger;ledger.add_input({0x1000,2,2},1);ledger.add_return({0x2000,2,2});
            reject("string_lifetime_unknown",[&]{ledger.add_return({0x3000,2,2});});no_free(ledger);}

        {Ledger ledger;for(int i=0;i<32;++i)ledger.add_input({0,0,0},0);
            reject("string_budget_exceeded",[&]{ledger.add_input({0,0,0},0);});
            require(ledger.input_count()==32 && ledger.uncertain(),"33-й input должен сохранять предыдущие записи и карантинировать");no_free(ledger);}
        {Ledger ledger;for(int i=0;i<4;++i)ledger.add_input({std::uintptr_t{0x1000}+static_cast<std::uintptr_t>(i)*0x10000,32768,32768},32767);
            require(ledger.allocation_bytes()==max_input_bytes,"Actual capacity бюджет должен достигать ровно 256 КиБ");
            reject("string_budget_exceeded",[&]{ledger.add_input({0x50000,2,2},1);});no_free(ledger);
            require(ledger.retained_count()==4 && ledger.retained_bytes()==max_input_bytes,"Allocation budget отказ сохраняет известные ресурсы");}
        {Ledger ledger;reject("string_budget_exceeded",[&]{ledger.add_input({0x1000,2,131073},1);});no_free(ledger);}

        for(const bool throws:{false,true}) {
            Ledger ledger;ledger.add_input({0x1000,2,4},1);ledger.add_input({0x2000,2,4},1);ledger.add_return({0x3000,2,4});
            std::vector<std::uintptr_t> calls;
            const auto failed=ledger.release([&](std::uintptr_t pointer)->bool {
                calls.push_back(pointer);if(pointer!=0x3000)return true;
                if(throws)throw std::runtime_error("allocator fault");return false;
            });
            require(calls==std::vector<std::uintptr_t>{0x1000,0x2000,0x3000} && failed.attempted && !failed.confirmed && failed.uncertain,
                    "Free fault должен остановить очистку после первой неуспешной allocation");
            require(failed.released_count==2 && ledger.retained_count()==1 && ledger.retained_bytes()==8,
                    "Неосвобождённые allocation должны сохраняться в карантине");
            no_free(ledger);require(calls.size()==3,"Free после fault не должен повторяться");
        }
        {Ledger ledger;ledger.add_input({0x1000,2,2},1);int calls{};ledger.release([&](std::uintptr_t){++calls;return true;});
            reject("string_lifetime_unknown",[&]{ledger.add_input({0x2000,2,2},1);});
            require(calls==1 && ledger.retained_count()==0,"Adopt после начала cleanup не должен повторно создавать ownership");no_free(ledger);}
        {Ledger ledger;ledger.add_input({0x1000,2,2},1);ledger.release([](std::uintptr_t){return true;});
            reject("string_lifetime_unknown",[&]{ledger.add_return({0x2000,2,2});});no_free(ledger);}
        std::cout<<"string_input_lifetime: "<<checks<<" checks passed\n";return 0;
    } catch(const std::exception& error) {std::cerr<<error.what()<<"\n";return 1;}
}
