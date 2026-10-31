#include <g1r_mcp/array_page.hpp>
#include <iostream>
#include <stdexcept>

namespace {
void require(bool value,const char* message){if(!value)throw std::runtime_error(message);}
template<class Operation>void rejects(const char* code,Operation operation) {
    try {operation();} catch(const g1r_mcp::HandlerError& error) {
        require(error.code()==code,"Неожиданный код ошибки массива");return;
    }
    throw std::runtime_error("Ожидался отказ массива");
}
}
int main() {
    try {
        const g1r_mcp::ArrayHeader header{0x1000,205,256};
        const auto first=g1r_mcp::array_page(header,4,1,0,200);
        require(first.begin==0x1000 && first.bytes==800 && first.count==200 && first.has_more,"Неверная первая страница");
        const auto tail=g1r_mcp::array_page(header,4,1,200,200);
        require(tail.begin==0x1320 && tail.count==5 && tail.bytes==20 && !tail.has_more,"Неверная последняя страница");
        const auto eof=g1r_mcp::array_page(header,4,1,205,64);
        require(eof.count==0 && eof.bytes==0 && !eof.has_more,"EOF не пустой");
        require(g1r_mcp::array_page({},8,1,0,64).count==0,"Пустой массив отклонён");
        require(g1r_mcp::array_page({0x1000,0,8},8,1,0,64).count==0,"Пустой массив с резервом отклонён");
        rejects("invalid_array_page",[&]{g1r_mcp::array_page(header,4,1,-1,64);});
        rejects("invalid_array_page",[&]{g1r_mcp::array_page(header,4,1,206,64);});
        rejects("invalid_array_page",[&]{g1r_mcp::array_page(header,4,1,0,201);});
        rejects("unsupported_array_layout",[&]{g1r_mcp::array_page(header,4,2,0,64);});
        rejects("unsupported_array_layout",[&]{g1r_mcp::array_page(header,0,1,0,64);});
        rejects("invalid_array_header",[]{g1r_mcp::array_page({0x1000,-1,1},4,1,0,64);});
        rejects("invalid_array_header",[]{g1r_mcp::array_page({0x1000,2,1},4,1,0,64);});
        rejects("invalid_array_header",[]{g1r_mcp::array_page({0,1,1},4,1,0,64);});
        rejects("invalid_array_header",[]{g1r_mcp::array_page({0x1000,1,1000001},4,1,0,64);});
        rejects("invalid_array_header",[]{g1r_mcp::array_page({std::numeric_limits<std::uintptr_t>::max()-8,2,2},8,1,0,64);});
        rejects("array_page_too_large",[]{g1r_mcp::array_page({0x1000,200,200},1024,1,0,200);});
        require(g1r_mcp::same_array_header(header,header),"Одинаковый заголовок не сравнивается");
        require(!g1r_mcp::same_array_header(header,{0x2000,205,256}),"Смена адреса не замечена");
        require(!g1r_mcp::same_array_header(header,{0x1000,204,256}),"Смена размера не замечена");
        require(!g1r_mcp::same_array_header(header,{0x1000,205,512}),"Смена ёмкости не замечена");
        std::cout<<"Проверки страниц массива пройдены\n";return 0;
    } catch(const std::exception& error){std::cerr<<error.what()<<'\n';return 1;}
}
