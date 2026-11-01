#pragma once

#include <g1r_mcp/string_arguments.hpp>
#include <g1r_mcp/string_return.hpp>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <limits>
#include <utility>
#include <vector>

namespace g1r_mcp::string_input_lifetime {

class InputBudget {
public:
    void add(std::size_t units) {
        if (units > string_arguments::max_utf16_units)
            throw HandlerError("invalid_argument", "FString с терминатором не помещается в int32");
        const auto bytes = (units + 1) * sizeof(char16_t);
        if (bytes > std::numeric_limits<std::size_t>::max() - bytes_)
            throw HandlerError("string_size_overflow", "Размер памяти FString не помещается в size_t");
        ++count_;
        bytes_ += bytes;
    }
    std::size_t input_count() const noexcept {return count_;}
    std::size_t allocation_bytes() const noexcept {return bytes_;}
private:
    std::size_t count_{},bytes_{};
};

struct Cleanup {
    bool attempted{},confirmed{},uncertain{};
    std::size_t released_count{};
};

inline bool same_header(const StringHeader& left, const StringHeader& right) noexcept {
    return left.data == right.data && left.count == right.count && left.capacity == right.capacity;
}

// Записи появляются только после подтверждённого создания/возврата FString.
// Деструктор ledger ничего не освобождает: сомнительное владение карантинируется.
class Ledger {
public:
    Ledger() = default;
    Ledger(const Ledger&) = delete;
    Ledger& operator=(const Ledger&) = delete;
    Ledger(Ledger&&) = delete;
    Ledger& operator=(Ledger&&) = delete;

    // Выделение host-памяти выполняется до создания первого игрового FString.
    void prepare(std::size_t input_count) {
        inputs_.resize(input_count);
        allocations_.resize(input_count+1);
    }

    void add_input(const StringHeader& header, std::size_t source_units) {
        if (uncertain_) throw_unknown();
        if (release_started_) fail("string_lifetime_unknown", "FString нельзя регистрировать после начала очистки");
        if (source_units > string_arguments::max_utf16_units)
            fail("invalid_argument", "FString с терминатором не помещается в int32");
        if (input_count_ >= inputs_.size())
            fail("string_lifetime_unknown", "Учёт FString входов не подготовлен до создания строки");
        validate_owned_header(header);
        const auto expected_count = source_units ? static_cast<std::int32_t>(source_units + 1) : 0;
        if (header.count != expected_count || (!source_units && (header.data || header.capacity)))
            fail("string_lifetime_unknown", "Конструктор FString вернул неожиданную длину");
        const auto bytes = allocation_size(header);
        if (bytes > std::numeric_limits<std::size_t>::max() - input_bytes_)
            fail("string_size_overflow", "Размер памяти FString не помещается в size_t");
        if (header.data) add_allocation(header, false);
        inputs_[input_count_++] = header;
        input_bytes_ += bytes;
    }

    // Точное совпадение диапазона — один owned allocation, включая return alias.
    // Частичное совпадение или один указатель с разной ёмкостью недопустимы.
    void add_return(const StringHeader& header) {
        if (uncertain_) throw_unknown();
        if (release_started_) fail("string_lifetime_unknown", "FString результат нельзя регистрировать после начала очистки");
        if (return_added_) fail("string_lifetime_unknown", "FString результат зарегистрирован повторно");
        validate_owned_header(header);
        if (header.data) add_allocation(header, true);
        return_added_ = true;
    }

    void validate_unchanged_input(std::size_t index, const StringHeader& current) {
        if (index >= input_count_ || !same_header(inputs_[index], current))
            fail("string_lifetime_unknown", "Заголовок const FString входа изменился");
        if (uncertain_) throw_unknown();
    }

    void mark_uncertain() noexcept {uncertain_ = true;}
    bool uncertain() const noexcept {return uncertain_;}
    std::size_t input_count() const noexcept {return input_count_;}
    std::size_t allocation_count() const noexcept {return allocation_count_;}
    std::size_t allocation_bytes() const noexcept {return allocation_bytes_;}
    std::size_t retained_count() const noexcept {return allocation_count_-released_count_;}
    std::size_t retained_bytes() const noexcept {return allocation_bytes_-released_bytes_;}

    template<class Releaser> Cleanup release(Releaser&& releaser) noexcept {
        Cleanup result{false, false, uncertain_, released_count_};
        if (uncertain_) return result;
        release_started_ = true;
        for (std::size_t i = 0; i < allocation_count_; ++i) {
            if (allocations_[i].released) continue;
            result.attempted = true;
            bool confirmed{};
            try {confirmed = std::invoke(releaser, allocations_[i].header.data);}
            catch (...) {confirmed = false;}
            if (!confirmed) {
                uncertain_ = true;
                result.uncertain = true;
                return result;
            }
            allocations_[i].released = true;
            ++released_count_;
            released_bytes_ += allocation_size(allocations_[i].header);
            result.released_count = released_count_;
        }
        result.confirmed = true;
        return result;
    }

private:
    struct Allocation {StringHeader header{};bool released{};};
    std::vector<StringHeader> inputs_;
    std::vector<Allocation> allocations_;
    std::size_t input_count_{},input_bytes_{},allocation_count_{},allocation_bytes_{};
    std::size_t released_count_{},released_bytes_{};
    bool uncertain_{},return_added_{},release_started_{};

    [[noreturn]] static void throw_unknown() {
        throw HandlerError("string_lifetime_unknown", "Владение FString не подтверждено; освобождение запрещено");
    }
    [[noreturn]] void fail(const char* code,const char* message) {
        uncertain_ = true;
        throw HandlerError(code, message);
    }
    static std::size_t allocation_size(const StringHeader& header) noexcept {
        return static_cast<std::size_t>(header.capacity) * sizeof(char16_t);
    }
    void validate_owned_header(const StringHeader& header) {
        try {validate_string_header(header);}
        catch (...) {uncertain_ = true;throw;}
    }
    void add_allocation(const StringHeader& header, bool allow_alias) {
        const auto bytes = allocation_size(header);
        const auto end = header.data + bytes;
        for (std::size_t i = 0; i < allocation_count_; ++i) {
            const auto& original = allocations_[i].header;
            const auto original_end = original.data + allocation_size(original);
            if (header.data >= original_end || original.data >= end) continue;
            if (allow_alias && header.data == original.data && header.capacity == original.capacity) return;
            fail("string_lifetime_unknown", "FString allocation имеет неподтверждённый алиас");
        }
        if (allocation_count_ >= allocations_.size()
            || bytes > std::numeric_limits<std::size_t>::max() - allocation_bytes_)
            fail("string_lifetime_unknown", "Учёт владения FString превышает предел");
        allocations_[allocation_count_++].header = header;
        allocation_bytes_ += bytes;
    }
};

} // namespace g1r_mcp::string_input_lifetime
