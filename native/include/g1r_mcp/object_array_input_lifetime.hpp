#pragma once

#include <g1r_mcp/array_page.hpp>
#include <array>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <limits>

namespace g1r_mcp::object_array_input_lifetime {

inline constexpr std::size_t max_arrays = 32;
inline constexpr std::size_t max_elements = 32;
inline constexpr std::size_t element_size = 8;
inline constexpr std::size_t max_allocation_bytes = 8192;

class InputBudget {
public:
    void add(std::size_t count) {
        if (count > max_elements || count_ >= max_arrays)
            throw HandlerError("array_budget_exceeded", "Превышен предел количества или длины объектных массивов");
        const auto bytes = count * element_size;
        if (bytes > max_allocation_bytes - bytes_)
            throw HandlerError("array_budget_exceeded", "Объектные массивы превышают 8192 байта");
        ++count_;
        bytes_ += bytes;
    }

    std::size_t input_count() const noexcept {return count_;}
    std::size_t allocation_bytes() const noexcept {return bytes_;}

private:
    std::size_t count_{}, bytes_{};
};

struct Cleanup {
    bool attempted{}, confirmed{}, uncertain{};
    std::size_t released_count{};
};

// Ledger владеет только выделенными игровым Realloc буферами указателей.
// Сами UObject не приобретают владельца и никогда не передаются в Free.
// Проверку байтов элементов и serial identity выполняет вызывающий код.
class Ledger {
public:
    Ledger() = default;
    Ledger(const Ledger&) = delete;
    Ledger& operator=(const Ledger&) = delete;
    Ledger(Ledger&&) = delete;
    Ledger& operator=(Ledger&&) = delete;

    void add_input(const ArrayHeader& header, std::size_t expected_count) {
        if (uncertain_) throw_unknown();
        if (release_started_)
            fail("array_lifetime_unknown", "Массив нельзя регистрировать после начала очистки");
        if (input_count_ >= max_arrays || expected_count > max_elements)
            fail("array_budget_exceeded", "Превышен предел количества или длины объектных массивов");
        validate_header(header, expected_count);
        const auto bytes = expected_count * element_size;
        if (bytes > max_allocation_bytes - allocation_bytes_)
            fail("array_budget_exceeded", "Выделенные объектные массивы превышают 8192 байта");
        if (header.data) add_allocation(header);
        inputs_[input_count_++] = header;
    }

    void validate_unchanged_input(std::size_t index, const ArrayHeader& current) {
        if (uncertain_) throw_unknown();
        if (index >= input_count_ || !same_array_header(inputs_[index], current))
            fail("array_lifetime_unknown", "Заголовок const объектного массива изменился");
    }

    void mark_uncertain() noexcept {uncertain_ = true;}
    bool uncertain() const noexcept {return uncertain_;}
    std::size_t input_count() const noexcept {return input_count_;}
    std::size_t allocation_count() const noexcept {return allocation_count_;}
    std::size_t allocation_bytes() const noexcept {return allocation_bytes_;}
    std::size_t retained_count() const noexcept {return allocation_count_ - released_count_;}
    std::size_t retained_bytes() const noexcept {return allocation_bytes_ - released_bytes_;}

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
    struct Allocation {ArrayHeader header{}; bool released{};};
    std::array<ArrayHeader, max_arrays> inputs_{};
    std::array<Allocation, max_arrays> allocations_{};
    std::size_t input_count_{}, allocation_count_{}, allocation_bytes_{};
    std::size_t released_count_{}, released_bytes_{};
    bool uncertain_{}, release_started_{};

    [[noreturn]] static void throw_unknown() {
        throw HandlerError("array_lifetime_unknown", "Владение буфером массива не подтверждено; освобождение запрещено");
    }

    [[noreturn]] void fail(const char* code, const char* message) {
        uncertain_ = true;
        throw HandlerError(code, message);
    }

    static std::size_t allocation_size(const ArrayHeader& header) noexcept {
        return static_cast<std::size_t>(header.capacity) * element_size;
    }

    void validate_header(const ArrayHeader& header, std::size_t expected_count) {
        const auto count = static_cast<std::int32_t>(expected_count);
        if (header.count != count || header.capacity != count
            || (!count && header.data) || (count && !header.data)
            || (header.data && header.data % element_size))
            fail("invalid_array_header", "Объектный массив требует точный count/capacity и выровненный игровой буфер");
        const auto bytes = expected_count * element_size;
        if (header.data > std::numeric_limits<std::uintptr_t>::max() - bytes)
            fail("invalid_array_header", "Адресный диапазон объектного массива переполняется");
    }

    void add_allocation(const ArrayHeader& header) {
        const auto bytes = allocation_size(header);
        const auto end = header.data + bytes;
        for (std::size_t i = 0; i < allocation_count_; ++i) {
            const auto& original = allocations_[i].header;
            const auto original_end = original.data + allocation_size(original);
            if (header.data >= original_end || original.data >= end) continue;
            fail("array_lifetime_unknown", "Объектные массивы имеют неподтверждённый алиас буфера");
        }
        if (allocation_count_ >= allocations_.size())
            fail("array_lifetime_unknown", "Учёт владения объектными массивами превышает предел");
        allocations_[allocation_count_++].header = header;
        allocation_bytes_ += bytes;
    }
};

} // namespace g1r_mcp::object_array_input_lifetime
