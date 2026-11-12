#pragma once

#include "lob/Types.h"

#include <cstddef>
#include <list>
#include <map>
#include <optional>
#include <unordered_map>
#include <vector>

namespace lob {

// Price levels in a std::map, orders at each level in a std::list (FIFO).
class MapBook {
public:
    static constexpr const char *name = "MapBook";

    explicit MapBook(EventSink &sink);

    Result addOrder(const Order &order);
    Result cancelOrder(OrderId id);
    // Same price and a smaller or equal quantity keeps queue position. Any
    // other change cancels the order and re-adds it as a Limit order at the
    // back of its new level.
    Result modifyOrder(OrderId id, Quantity newQuantity, Price newPrice);

    std::optional<Price> bestBid() const;
    std::optional<Price> bestAsk() const;
    std::optional<Price> spread() const;
    std::vector<LevelInfo> depth(Side side, std::size_t levels) const;
    Quantity volumeAtPrice(Side side, Price price) const;
    std::size_t orderCount() const { return index_.size(); }
    bool checkInvariants() const;

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

    struct Locator {
        Side side;
        Levels::iterator level;
        std::list<Resting>::iterator order;
    };

    Levels &levels(Side side) { return side == Side::Buy ? bids_ : asks_; }
    const Levels &levels(Side side) const { return side == Side::Buy ? bids_ : asks_; }

    Result reject(const Order &order, RejectReason reason);
    Result enter(const Order &order);
    bool crosses(const Order &order, Price restingPrice) const;
    Quantity fillableQuantity(const Order &order) const;
    Result match(Order &order);
    void rest(const Order &order);
    void remove(const Locator &locator);
    void emit(EventType type, RejectReason reason, const Order &order, OrderId restingId,
              Price price, Quantity quantity);

    EventSink &sink_;
    Levels bids_{BestFirst{true}};
    Levels asks_{BestFirst{false}};
    std::unordered_map<OrderId, Locator> index_;
};

} // namespace lob
