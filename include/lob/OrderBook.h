#pragma once
#include "Order.h"
#include <map>
#include <list>
#include <unordered_set>
#include <cstddef>
#include <functional>

enum class AddStatus { Accepted, Rejected };

enum class RejectReason { None, ZeroQuantity, InvalidPrice, DuplicateId };

struct AddResult {
    AddStatus status;
    RejectReason reason;
    std::size_t fillCount;
    Quantity filledQuantity;
};

class OrderBook {
public:
    // Maps price to a list of orders (Price-Time Priority)
    // Bids: Highest price first (std::greater)
    // Asks: Lowest price first (std::less)
    // We use std::list for O(1) insertions/deletions at the ends (FIFO queue)
    using Bids = std::map<Price, std::list<Order>, std::greater<Price>>;
    using Asks = std::map<Price, std::list<Order>, std::less<Price>>;

    explicit OrderBook(TradeSink& sink);

    // Core matching logic
    AddResult addOrder(Order order);

    // Getters for analysis
    bool isBidEmpty() const;
    bool isAskEmpty() const;
    const Bids& getBids() const;
    const Asks& getAsks() const;

private:
    Bids bids_;
    Asks asks_;
    TradeSink& sink_;
    std::unordered_set<OrderId> restingIds_;

    // Internal helpers
    std::size_t matchAsk(Order& bidOrder);
    std::size_t matchBid(Order& askOrder);
};