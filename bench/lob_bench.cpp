#include "lob/MapBook.h"

#include <benchmark/benchmark.h>

#include <algorithm>
#include <cstddef>
#include <random>
#include <vector>

namespace {

using namespace lob;

struct CountingSink : EventSink {
    std::size_t trades = 0;
    void onEvent(const Event& event) override { trades += event.type == EventType::Fill; }
};

// Random walk around $100.00 (10000 ticks) with steps of up to 50 ticks.
std::vector<Order> randomWalkOrders(std::size_t count) {
    std::mt19937 rng(42);
    std::uniform_int_distribution<int> sideDist(0, 1);
    std::uniform_int_distribution<int> stepDist(-50, 50);
    std::uniform_int_distribution<int> qtyDist(1, 100);

    std::vector<Order> orders;
    orders.reserve(count);
    Price mid = 10000;
    for (OrderId id = 1; id <= count; ++id) {
        const Side side = sideDist(rng) == 0 ? Side::Buy : Side::Sell;
        mid = std::max<Price>(100, mid + stepDist(rng));
        const Price price = mid + (side == Side::Buy ? -5 : 5);
        orders.push_back({id, side, price, static_cast<Quantity>(qtyDist(rng))});
    }
    return orders;
}

void BM_RandomWalkAdds(benchmark::State& state) {
    const auto orders = randomWalkOrders(static_cast<std::size_t>(state.range(0)));
    std::size_t trades = 0;
    for (auto _ : state) {
        state.PauseTiming();
        CountingSink sink;
        MapBook book(sink);
        state.ResumeTiming();
        for (const Order& order : orders) {
            benchmark::DoNotOptimize(book.addOrder(order));
        }
        trades = sink.trades;
    }
    state.SetItemsProcessed(state.iterations() * state.range(0));
    state.counters["trades"] = static_cast<double>(trades);
}

} // namespace

BENCHMARK(BM_RandomWalkAdds)->Arg(1'000'000)->Unit(benchmark::kMillisecond);

BENCHMARK_MAIN();
