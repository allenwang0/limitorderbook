#pragma once

#include "lob/Types.h"

#include <cstddef>
#include <list>
#include <map>
#include <optional>
#include <unordered_set>
#include <vector>

namespace lob {

// Price levels in a std::map, orders at each level in a std::list (FIFO).
class MapBook {
public:
    static constexpr const char* name = "MapBook";

    explicit MapBook(EventSink& sink);

    Result addOrder(const Order& order);

    std::optional<Price> bestBid() const;
    std::optional<Price> bestAsk() const;
    std::optional<Price> spread() const;
    std::vector<LevelInfo> depth(Side side, std::size_t levels) const;
    Quantity volumeAtPrice(Side side, Price price) const;

private:
    struct Resting {
        OrderId id;
        Quantity quantity;
    };

    struct Level {
        std::list<Resting> orders;
        Quantity quantity = 0;
    };

    // One comparator type for both sides so begin() is the best level on each.
    struct BestFirst {
        bool descending;
        bool operator()(Price a, Price b) const { return descending ? a > b : a < b; }
    };

    using Levels = std::map<Price, Level, BestFirst>;

    Levels& levels(Side side) { return side == Side::Buy ? bids_ : asks_; }
    const Levels& levels(Side side) const { return side == Side::Buy ? bids_ : asks_; }

    Result match(Order& order);
    void emit(EventType type, RejectReason reason, const Order& order, OrderId restingId,
              Price price, Quantity quantity);

    EventSink& sink_;
    Levels bids_{BestFirst{true}};
    Levels asks_{BestFirst{false}};
    std::unordered_set<OrderId> restingIds_;
};

} // namespace lob
