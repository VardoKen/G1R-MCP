#include <g1r_mcp/as_exception_capture.hpp>

#include <iostream>
#include <stdexcept>
#include <thread>
#include <type_traits>

namespace {
int checks{};
void require(bool good,const char* message) {++checks;if(!good)throw std::runtime_error(message);}

void record_text(const std::string& text) {
    const auto begin=reinterpret_cast<std::uintptr_t>(text.c_str());
    g1r_mcp::as_exception_capture::record(text.c_str(),[&](std::uintptr_t address,char& value) {
        if(address<begin || address>begin+text.size())return false;
        value=*reinterpret_cast<const char*>(address);return true;
    });
}
}

int main() {
    using namespace g1r_mcp::as_exception_capture;
    static_assert(!std::is_copy_constructible_v<Scope> && !std::is_copy_assignable_v<Scope>);
    try {
        int outside_reads{};
        record("outside",[&](std::uintptr_t,char&){++outside_reads;return false;});
        require(active==nullptr && outside_reads==0,"Logger вне ProcessEvent не должен читать и сохранять сообщения");
        State outer;
        {
            Scope scope(outer);
            record_text("outer first");
            State inner;
            {
                Scope nested(inner);
                record_text("inner");
                require(inner.exception_count==1 && inner.messages==std::vector<std::string>{"inner"},
                        "Вложенный ProcessEvent должен принадлежать собственной записи");
                require(outer.exception_count==1,"Вложенное сообщение не должно попадать во внешний вызов");
            }
            record_text("outer second");
            require(active==&outer && outer.messages==std::vector<std::string>{"outer first","outer second"},
                    "Завершение вложенного вызова должно возвращать прежнего владельца");
            bool isolated{};
            std::thread worker([&] {
                isolated=active==nullptr;
                record_text("foreign thread");
                State own;
                {Scope local(own);record_text("own thread");}
                isolated=isolated && own.exception_count==1 && own.messages==std::vector<std::string>{"own thread"} && active==nullptr;
            });
            worker.join();
            require(isolated && outer.exception_count==2,"Другой поток не должен изменять текущую запись ProcessEvent");
        }
        require(active==nullptr && outer.full_capture,"Завершение ProcessEvent должно убрать TLS владельца");
        {
            State state;
            {Scope scope(state);
                const std::string long_message(65536,'x');
                for(int i=0;i<65;++i)record_text(i==64?long_message:std::string("message ")+std::to_string(i));
            }
            require(state.exception_count==65 && state.messages.size()==65 && state.messages.back().size()==65536 && state.full_capture,
                    "Захват сообщений не должен ограничивать количество или длину");
        }
        {
            State state;
            {Scope scope(state);
                record(nullptr,[](std::uintptr_t,char&)->bool {throw std::runtime_error("null must not read");});
                record_text("");
                record_text(std::string("raw\xff",4));
            }
            require(state.messages==std::vector<std::string>{"NO EXCEPTION","",std::string("raw\xff",4)} && state.exception_count==3 && state.full_capture,
                    "Null, пустое сообщение и ANSI байты должны сохраняться без выдуманного текста или преобразования");
        }
        for(const bool throws:{false,true}) {
            State state;
            {Scope scope(state);
                const std::string text="partial message";
                const auto begin=reinterpret_cast<std::uintptr_t>(text.c_str());
                record(text.c_str(),[&](std::uintptr_t address,char& value) {
                    if(address==begin+3) {
                        if(throws)throw std::bad_alloc();
                        return false;
                    }
                    value=*reinterpret_cast<const char*>(address);return true;
                });
                record_text("next complete");
            }
            require(state.exception_count==2 && !state.full_capture && state.messages==std::vector<std::string>{"next complete"},
                    "Ошибка чтения или выделения памяти должна сохранить факт исключения и не публиковать частичное сообщение");
        }
        {
            State state;
            try {Scope scope(state);record_text("before C++ error");throw std::runtime_error("scope exit");}
            catch(const std::runtime_error&) {}
            require(active==nullptr && state.exception_count==1,"Выход с C++ ошибкой должен восстанавливать TLS владельца");
        }
        {
            State state;
            state.exception_count=3;
            state.messages={"ASCII exception","Ошибка скрипта",std::string("emoji \xf0\x9f\x98\x80",10)};
            const auto diagnostic=describe(state);
            const nlohmann::json expected={{"source","observed_logging_entry"},{"exception_count",3},
                {"full_capture",true},{"stack_captured",false},{"messages",state.messages}};
            require(diagnostic==expected && nlohmann::json::parse(diagnostic.dump())==expected,
                    "Корректный UTF-8 должен передаваться полностью без изменения и fabricated stack");
        }
        {
            State state;
            state.exception_count=4;state.full_capture=false;
            state.messages={"first",std::string("raw\xff",4),"third",std::string("\xc3\x28",2)};
            const auto original=state.messages;
            const auto diagnostic=describe(state);
            const nlohmann::json expected={{"source","observed_logging_entry"},{"exception_count",4},
                {"full_capture",false},{"stack_captured",false},
                {"messages",nlohmann::json::array({"first",nullptr,"third",nullptr})},
                {"message_bytes",nlohmann::json::array({{{"index",1},{"encoding","hex"},{"bytes","726177ff"}},
                    {{"index",3},{"encoding","hex"},{"bytes","c328"}}})}};
            require(diagnostic==expected && nlohmann::json::parse(diagnostic.dump())==expected && state.messages==original,
                    "Некорректные ANSI байты должны сохраняться точным hex с исходными индексами без замены и потери сообщений");
        }
        {
            State state;
            state.exception_count=2;state.full_capture=false;
            const auto diagnostic=describe(state);
            require(diagnostic.at("exception_count")==2 && !diagnostic.at("full_capture").get<bool>()
                    && diagnostic.at("messages").empty() && !diagnostic.contains("message_bytes"),
                    "Ошибка захвата должна сохранять факт исключений даже без скопированных сообщений");
        }
        std::cout<<"as_exception_capture: "<<checks<<" checks passed\n";return 0;
    } catch(const std::exception& error) {std::cerr<<error.what()<<"\n";return 1;}
}
