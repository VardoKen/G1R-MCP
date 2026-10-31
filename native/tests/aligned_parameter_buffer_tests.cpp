#include <g1r_mcp/aligned_parameter_buffer.hpp>

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <limits>
#include <memory>
#include <new>
#include <stdexcept>
#include <vector>

namespace {
using g1r_mcp::aligned_parameter_buffer::Buffer;
using nlohmann::json;
int checks{};

void require(bool condition,const char* message) {
    ++checks;
    if(!condition)throw std::runtime_error(message);
}

void reject_extent(std::size_t extent) {
    try {Buffer rejected(extent);}
    catch(const g1r_mcp::HandlerError& error) {
        require(error.code()=="unsupported_signature","Неверный код отказа размера буфера");
        require(error.details()==json{{"executed",false},{"may_have_executed",false},{"do_not_retry",false}},
                "Неверный extent должен отвергаться до исполнения");
        return;
    }
    throw std::runtime_error("Ожидался отказ слишком большого extent");
}

void require_alignment(const Buffer& buffer) {
    require(reinterpret_cast<std::uintptr_t>(buffer.data())%16==0,
            "Реальный адрес параметров должен иметь выравнивание 16");
}

void check_zero_storage(std::size_t extent) {
    // До placement construction все байты грязные. Это проверяет обнуление
    // всей ёмкости, а не случайно нулевую память свежего стека/allocator.
    alignas(Buffer) std::array<unsigned char,sizeof(Buffer)+64> guarded;
    guarded.fill(0xa5);
    auto* buffer=std::construct_at(reinterpret_cast<Buffer*>(guarded.data()+32),extent);
    require_alignment(*buffer);
    require(buffer->bytes().size()==extent,"Span должен ограничиваться текущим extent");
    const Buffer& readonly=*buffer;
    require(readonly.bytes().size()==extent,"Const span должен иметь тот же ограниченный extent");
    require(std::all_of(readonly.data(),readonly.data()+4096,[](unsigned char value){return value==0;}),
            "Все 4096 байт, включая padding за extent, должны быть нулевыми");

    const auto canaries_unchanged=[&] {
        return std::all_of(guarded.begin(),guarded.begin()+32,[](unsigned char value){return value==0xa5;})
            && std::all_of(guarded.begin()+32+sizeof(Buffer),guarded.end(),[](unsigned char value){return value==0xa5;});
    };
    require(canaries_unchanged(),"Конструктор не должен писать за границы Buffer");
    if(extent) {
        buffer->bytes().front()=std::byte{0x11};
        buffer->bytes().back()=std::byte{0x22};
        require(std::all_of(readonly.data()+extent,readonly.data()+4096,[](unsigned char value){return value==0;}),
                "Запись в разрешённый span не должна изменять padding за extent");
        require(readonly.data()[extent-1]==0x22,"Последний разрешённый байт должен сохранять запись");
        if(extent>1)require(readonly.data()[0]==0x11,"Первый разрешённый байт должен сохранять независимую запись");
    }
    require(canaries_unchanged(),"Запись на границах span не должна изменять внешние canaries");
    std::destroy_at(buffer);
    require(canaries_unchanged(),"Завершение жизни Buffer не должно изменять canaries");
}
}

int main() {
    try {
        for(const auto extent:std::array<std::size_t,4>{0,1,4095,4096})check_zero_storage(extent);
        for(const auto extent:std::array<std::size_t,4>{4097,8192,
                std::numeric_limits<std::size_t>::max()-1,std::numeric_limits<std::size_t>::max()})
            reject_extent(extent);

        Buffer source(4096);
        source.bytes().front()=std::byte{0x31};
        source.bytes().back()=std::byte{0x73};
        Buffer copied(source);
        require_alignment(copied);
        require(copied.data()!=source.data(),"Copy construction должен владеть независимым хранилищем");
        require(std::equal(source.data(),source.data()+4096,copied.data()),
                "Copy construction должен сохранять всю ёмкость параметров");
        copied.bytes().front()=std::byte{0x44};
        source.bytes().back()=std::byte{0x55};
        require(source.data()[0]==0x31 && copied.data()[4095]==0x73,
                "Изменение копии и исходника не должно затрагивать другое хранилище");

        Buffer assigned(1);
        assigned=source;
        require_alignment(assigned);
        require(assigned.bytes().size()==4096 && assigned.data()!=source.data(),
                "Copy assignment должен переносить extent без alias исходного буфера");
        assigned.bytes()[4095]=std::byte{0x66};
        require(source.data()[4095]==0x55,"Запись assignment-копии должна оставаться независимой");

        // Реальное размещение в контейнере и перенос его allocator также
        // должны сохранять адреса, пригодные для параметров SIMD/FTransform.
        std::vector<Buffer> buffers;
        buffers.reserve(1);
        buffers.emplace_back(4096);
        buffers[0].bytes()[4095]=std::byte{0x7f};
        buffers.emplace_back(1);
        buffers.emplace_back(4095);
        buffers.emplace_back(0);
        for(const auto& buffer:buffers)require_alignment(buffer);
        require(buffers[0].data()[4095]==0x7f,"Перенос vector должен сохранять полную копию параметров");
        require(std::all_of(buffers[1].data(),buffers[1].data()+4096,[](unsigned char value){return value==0;}),
                "Перенос соседнего буфера не должен загрязнять нулевую ёмкость");

        std::cout<<"aligned_parameter_buffer: "<<checks<<" checks passed\n";
        return 0;
    } catch(const std::exception& error) {
        std::cerr<<error.what()<<"\n";
        return 1;
    }
}
