#include "NaiveBook.h"
#include "RecordingSink.h"
#include "lob/FlatBook.h"
#include "lob/MapBook.h"

#include <gtest/gtest.h>

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <random>
#include <sstream>
#include <string>
#include <vector>

namespace lob::test {
namespace {

std::size_t envOr(const char* name, std::size_t fallback) {
    const char* value = std::getenv(name);
    return value != nullptr ? std::stoul(value) : fallback;
}

struct Op {
    enum class Kind { Add, Cancel, Modify } kind;
    Order order;
};

std::string describe(const Op& op) {
    std::ostringstream out;
    const char* kinds[] = {"ADD", "CXL", "MOD"};
    out << kinds[static_cast<int>(op.kind)] << " id=" << op.order.id
        << " side=" << (op.order.side == Side::Buy ? 'B' : 'S') << " px=" << op.order.price
        << " qty=" << op.order.quantity << " type=" << static_cast<int>(op.order.type);
    return out.str();
}

// Mixed flow around a slowly drifting mid. Most prices sit a few ticks from
// the mid; a few land far away so FlatBook's out-of-window path is used.
// Some ops are invalid on purpose (zero quantity, bad price, reused or
// unknown ids) to exercise every rejection path.
class FlowGenerator {
public:
    // Below this many resting orders only adds are generated, so depth(10)
    // compares books with several levels on each side.
    static constexpr std::size_t kMinRestingOrders = 40;

    explicit FlowGenerator(uint64_t seed) : rng_(seed) {}

    Op next(const std::vector<OrderId>& resting) {
        if (chance(0.05)) {
            mid_ += chance(0.5) ? 1 : -1;
        }
        const double roll = uniform();
        if (roll < 0.45 || resting.size() < kMinRestingOrders) {
            return add(resting);
        }
        if (roll < 0.75) {
            return {Op::Kind::Cancel, {pickId(resting), Side::Buy, 0, 0}};
        }
        if (roll < 0.90) {
            const Side side = chance(0.5) ? Side::Buy : Side::Sell;
            return {Op::Kind::Modify, {pickId(resting), side, price(side), quantity()}};
        }
        Order market = add(resting).order;
        market.type = OrderType::Market;
        market.price = chance(0.5) ? 0 : market.price;
        return {Op::Kind::Add, market};
    }

private:
    bool chance(double p) { return uniform() < p; }
    double uniform() { return std::uniform_real_distribution<double>(0.0, 1.0)(rng_); }

    Quantity quantity() {
        if (chance(0.01)) {
            return 0;
        }
        return std::uniform_int_distribution<Quantity>(1, 20)(rng_);
    }

    Price price(Side side) {
        if (chance(0.005)) {
            return -std::uniform_int_distribution<Price>(0, 5)(rng_);
        }
        if (chance(0.02)) {
            return mid_ + std::uniform_int_distribution<Price>(-5000, 5000)(rng_);
        }
        const Price distance = std::geometric_distribution<Price>(0.3)(rng_);
        const Price offset = chance(0.15) ? -distance : distance + 1;
        return side == Side::Buy ? mid_ - offset : mid_ + offset;
    }

    OrderId pickId(const std::vector<OrderId>& resting) {
        if (resting.empty() || chance(0.05)) {
            return std::uniform_int_distribution<OrderId>(1, nextId_ + 10)(rng_);
        }
        return resting[std::uniform_int_distribution<std::size_t>(0, resting.size() - 1)(rng_)];
    }

    Op add(const std::vector<OrderId>& resting) {
        const Side side = chance(0.5) ? Side::Buy : Side::Sell;
        const OrderId id = chance(0.02) ? pickId(resting) : nextId_++;
        OrderType type = OrderType::Limit;
        const double roll = uniform();
        if (roll < 0.10) {
            type = OrderType::ImmediateOrCancel;
        } else if (roll < 0.20) {
            type = OrderType::FillOrKill;
        } else if (roll < 0.30) {
            type = OrderType::PostOnly;
        }
        return {Op::Kind::Add, {id, side, price(side), quantity(), type}};
    }

    std::mt19937_64 rng_;
    Price mid_ = 10'000;
    OrderId nextId_ = 1;
};

template <class Book>
struct Runner {
    RecordingSink sink;
    Book book;

    template <class... Args>
    explicit Runner(Args... args) : book(sink, args...) {}

    Result apply(const Op& op) {
        sink.events.clear();
        switch (op.kind) {
        case Op::Kind::Add:
            return book.addOrder(op.order);
        case Op::Kind::Cancel:
            return book.cancelOrder(op.order.id);
        case Op::Kind::Modify:
            return book.modifyOrder(op.order.id, op.order.quantity, op.order.price);
        }
        return {};
    }
};

bool sameResult(const Result& a, const Result& b) {
    return a.status == b.status && a.reason == b.reason && a.fillCount == b.fillCount &&
           a.filledQuantity == b.filledQuantity;
}

template <class Book>
::testing::AssertionResult matchesReference(Runner<Book>& subject, const Result& result,
                                            Runner<NaiveBook>& reference, const Result& expected) {
    const char* name = Book::name;
    if (!sameResult(result, expected)) {
        return ::testing::AssertionFailure() << name << " result differs";
    }
    if (subject.sink.events != reference.sink.events) {
        return ::testing::AssertionFailure()
               << name << " events differ (" << subject.sink.events.size() << " vs "
               << reference.sink.events.size() << ")";
    }
    for (const Side side : {Side::Buy, Side::Sell}) {
        if (subject.book.depth(side, 10) != reference.book.depth(side, 10)) {
            return ::testing::AssertionFailure()
                   << name << (side == Side::Buy ? " bid" : " ask") << " depth(10) differs";
        }
    }
    if (subject.book.bestBid() != reference.book.bestBid() ||
        subject.book.bestAsk() != reference.book.bestAsk()) {
        return ::testing::AssertionFailure() << name << " top of book differs";
    }
    if (subject.book.orderCount() != reference.book.orderCount()) {
        return ::testing::AssertionFailure() << name << " order count differs";
    }
    if (!subject.book.checkInvariants()) {
        return ::testing::AssertionFailure() << name << " invariant violated";
    }
    return ::testing::AssertionSuccess();
}

TEST(Differential, BooksAgreeWithNaiveReference) {
    const std::size_t seeds = envOr("LOB_DIFF_SEEDS", 20);
    const std::size_t opsPerSeed = envOr("LOB_DIFF_OPS", 100'000);
    std::size_t opsChecked = 0;

    for (uint64_t seed = 1; seed <= seeds; ++seed) {
        FlowGenerator flow(seed);
        Runner<NaiveBook> reference;
        Runner<MapBook> map;
        Runner<FlatBook> flat;
        // A 16-tick window puts most orders in the std::map fallback and
        // moves the best price across the window edge constantly.
        Runner<FlatBook> narrowFlat(Price{9'992}, std::size_t{16});
        for (std::size_t i = 0; i < opsPerSeed; ++i) {
            const Op op = flow.next(reference.book.restingIds());
            const Result expected = reference.apply(op);
            const Result mapResult = map.apply(op);
            const Result flatResult = flat.apply(op);
            const Result narrowResult = narrowFlat.apply(op);
            ASSERT_TRUE(matchesReference(map, mapResult, reference, expected))
                << "seed " << seed << " op " << i << ": " << describe(op);
            ASSERT_TRUE(matchesReference(flat, flatResult, reference, expected))
                << "seed " << seed << " op " << i << ": " << describe(op);
            ASSERT_TRUE(matchesReference(narrowFlat, narrowResult, reference, expected))
                << "narrow window, seed " << seed << " op " << i << ": " << describe(op);
            ++opsChecked;
        }
    }
    RecordProperty("ops_checked", static_cast<int>(opsChecked));
    std::cout << "differential: " << seeds << " seeds x " << opsPerSeed << " ops = " << opsChecked
              << " ops, 0 mismatches\n";
}

} // namespace
} // namespace lob::test
