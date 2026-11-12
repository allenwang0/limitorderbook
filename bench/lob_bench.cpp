#include "lob/FlatBook.h"
#include "lob/MapBook.h"

#include <benchmark/benchmark.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <random>
#include <string>
#include <unordered_set>
#include <vector>

#ifndef LOB_GIT_SHA
#define LOB_GIT_SHA "unknown"
#endif
#ifndef LOB_CXX_FLAGS
#define LOB_CXX_FLAGS "unknown"
#endif
#ifndef LOB_CPU
#define LOB_CPU "unknown"
#endif

namespace {

using namespace lob;
using Clock = std::chrono::steady_clock;

// Message mix: 55% add, 40% cancel, 5% market. Cancel-heavy, as on venues
// where most orders are cancelled before they trade.
constexpr double kAddShare = 0.55;
constexpr double kCancelShare = 0.40;
// 10% of adds are priced through the touch so the flow has crossing adds.
constexpr double kCrossingAddShare = 0.10;
// Ticks away from the touch follow a geometric distribution with mean
// (1 - p) / p = 3 ticks, so most resting orders sit near the touch.
constexpr double kGeometricP = 0.25;
constexpr uint64_t kSeed = 20240611;
constexpr std::size_t kWarmupMessages = 100'000;
constexpr std::size_t kMeasuredMessages = 1'000'000;
constexpr Price kMid = 10'000;

struct Message {
    enum class Kind : uint8_t { Add, Cancel, Market } kind;
    Order order;
};

struct NullSink : EventSink {
    std::size_t fills = 0;
    void onEvent(const Event &event) override { fills += event.type == EventType::Fill; }
};

// Records orders filled away so generated cancels only target live orders.
struct FilledIdSink : EventSink {
    std::unordered_set<OrderId> removed;
    void onEvent(const Event &event) override {
        if (event.type == EventType::Fill) {
            removed.insert(event.restingId);
        }
    }
};

std::vector<Message> buildFlow(std::size_t count) {
    std::mt19937_64 rng(kSeed);
    std::uniform_real_distribution<double> unit(0.0, 1.0);
    std::geometric_distribution<Price> ticks(kGeometricP);
    std::uniform_int_distribution<Quantity> size(1, 100);

    FilledIdSink tracker;
    MapBook shadow(tracker);
    std::vector<OrderId> live;
    std::vector<Message> flow;
    flow.reserve(count);
    OrderId nextId = 1;

    for (std::size_t i = 0; i < count; ++i) {
        const double roll = unit(rng);
        const Side side = unit(rng) < 0.5 ? Side::Buy : Side::Sell;
        Message message{};
        if (roll < kCancelShare && !live.empty()) {
            std::uniform_int_distribution<std::size_t> pick(0, live.size() - 1);
            const std::size_t slot = pick(rng);
            message = {Message::Kind::Cancel, {live[slot], side, 0, 0}};
            live[slot] = live.back();
            live.pop_back();
            shadow.cancelOrder(message.order.id);
        } else if (roll < kCancelShare + kAddShare || live.empty()) {
            const Price touch = side == Side::Buy ? shadow.bestAsk().value_or(kMid + 1)
                                                  : shadow.bestBid().value_or(kMid - 1);
            const bool crossing = unit(rng) < kCrossingAddShare;
            const Price away = ticks(rng) + 1;
            Price price = side == Side::Buy ? touch - away : touch + away;
            if (crossing) {
                price = side == Side::Buy ? touch + ticks(rng) : touch - ticks(rng);
            }
            message = {Message::Kind::Add, {nextId++, side, std::max<Price>(1, price), size(rng)}};
            if (shadow.addOrder(message.order).filledQuantity < message.order.quantity) {
                live.push_back(message.order.id);
            }
        } else {
            message = {Message::Kind::Market, {nextId++, side, 0, size(rng), OrderType::Market}};
            shadow.addOrder(message.order);
        }
        if (!tracker.removed.empty()) {
            live.erase(std::remove_if(live.begin(), live.end(),
                                      [&](OrderId id) { return tracker.removed.count(id) != 0; }),
                       live.end());
            tracker.removed.clear();
        }
        flow.push_back(message);
    }
    return flow;
}

const std::vector<Message> &flow() {
    static const std::vector<Message> messages = buildFlow(kWarmupMessages + kMeasuredMessages);
    return messages;
}

template <class Book> Result apply(Book &book, const Message &message) {
    if (message.kind == Message::Kind::Cancel) {
        return book.cancelOrder(message.order.id);
    }
    return book.addOrder(message.order);
}

enum Category { AddResting, AddCrossing, Cancel, Market, kCategories };
constexpr std::array<const char *, kCategories> kCategoryNames = {"add_resting", "add_crossing",
                                                                  "cancel", "market"};

double percentile(std::vector<int64_t> &samples, double q) {
    const auto rank = static_cast<std::size_t>(q * static_cast<double>(samples.size() - 1));
    std::nth_element(samples.begin(), samples.begin() + static_cast<std::ptrdiff_t>(rank),
                     samples.end());
    return static_cast<double>(samples[rank]);
}

// One steady_clock sample per message. Adds are classed as resting or
// crossing after the fact, from whether they produced fills.
template <class Book> void BM_Latency(benchmark::State &state) {
    const auto &messages = flow();
    std::array<std::vector<int64_t>, kCategories> samples;
    for (auto _ : state) {
        for (auto &bucket : samples) {
            bucket.clear();
            bucket.reserve(kMeasuredMessages);
        }
        NullSink sink;
        Book book(sink);
        for (std::size_t i = 0; i < kWarmupMessages; ++i) {
            apply(book, messages[i]);
        }
        for (std::size_t i = kWarmupMessages; i < messages.size(); ++i) {
            const Message &message = messages[i];
            const auto start = Clock::now();
            Result result = apply(book, message);
            const auto stop = Clock::now();
            benchmark::DoNotOptimize(result);
            Category category = Market;
            if (message.kind == Message::Kind::Cancel) {
                category = Cancel;
            } else if (message.kind == Message::Kind::Add) {
                category = result.fillCount > 0 ? AddCrossing : AddResting;
            }
            samples[category].push_back(
                std::chrono::duration_cast<std::chrono::nanoseconds>(stop - start).count());
        }
    }
    for (std::size_t c = 0; c < kCategories; ++c) {
        auto &bucket = samples[c];
        if (bucket.empty()) {
            continue;
        }
        const std::string prefix = kCategoryNames[c];
        state.counters[prefix + "_count"] = static_cast<double>(bucket.size());
        state.counters[prefix + "_p50_ns"] = percentile(bucket, 0.50);
        state.counters[prefix + "_p90_ns"] = percentile(bucket, 0.90);
        state.counters[prefix + "_p99_ns"] = percentile(bucket, 0.99);
        state.counters[prefix + "_p99.9_ns"] = percentile(bucket, 0.999);
        state.counters[prefix + "_max_ns"] =
            static_cast<double>(*std::max_element(bucket.begin(), bucket.end()));
    }
}

template <class Book> void BM_Throughput(benchmark::State &state) {
    const auto &messages = flow();
    for (auto _ : state) {
        state.PauseTiming();
        NullSink sink;
        Book book(sink);
        for (std::size_t i = 0; i < kWarmupMessages; ++i) {
            apply(book, messages[i]);
        }
        state.ResumeTiming();
        for (std::size_t i = kWarmupMessages; i < messages.size(); ++i) {
            benchmark::DoNotOptimize(apply(book, messages[i]));
        }
    }
    state.SetItemsProcessed(state.iterations() * static_cast<int64_t>(kMeasuredMessages));
}

// A p50 close to the clock's tick or to the cost of reading it is not a
// real measurement, so report both.
void BM_ClockOverhead(benchmark::State &state) {
    std::vector<int64_t> empty;
    empty.reserve(kMeasuredMessages);
    int64_t tick = INT64_MAX;
    for (auto _ : state) {
        empty.clear();
        for (std::size_t i = 0; i < kMeasuredMessages; ++i) {
            const auto start = Clock::now();
            const auto stop = Clock::now();
            const int64_t delta =
                std::chrono::duration_cast<std::chrono::nanoseconds>(stop - start).count();
            empty.push_back(delta);
            if (delta > 0) {
                tick = std::min(tick, delta);
            }
        }
    }
    state.counters["timer_overhead_p50_ns"] = percentile(empty, 0.50);
    state.counters["clock_tick_ns"] = static_cast<double>(tick);
}

} // namespace

BENCHMARK(BM_ClockOverhead)->Iterations(1)->Unit(benchmark::kMillisecond);
BENCHMARK_TEMPLATE(BM_Latency, MapBook)->Iterations(1)->Unit(benchmark::kMillisecond);
BENCHMARK_TEMPLATE(BM_Latency, FlatBook)->Iterations(1)->Unit(benchmark::kMillisecond);
BENCHMARK_TEMPLATE(BM_Throughput, MapBook)->Unit(benchmark::kMillisecond);
BENCHMARK_TEMPLATE(BM_Throughput, FlatBook)->Unit(benchmark::kMillisecond);

int main(int argc, char **argv) {
    benchmark::AddCustomContext("cpu", LOB_CPU);
    benchmark::AddCustomContext("compiler", __VERSION__);
    benchmark::AddCustomContext("cxx_flags", LOB_CXX_FLAGS);
    benchmark::AddCustomContext("git_sha", LOB_GIT_SHA);
    benchmark::AddCustomContext("message_mix", "55% add, 40% cancel, 5% market");
    benchmark::AddCustomContext("seed", std::to_string(kSeed));
    benchmark::Initialize(&argc, argv);
    if (benchmark::ReportUnrecognizedArguments(argc, argv)) {
        return 1;
    }
    benchmark::RunSpecifiedBenchmarks();
    benchmark::Shutdown();
    return 0;
}
