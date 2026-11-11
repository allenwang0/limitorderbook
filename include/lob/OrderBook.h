#pragma once
#include "Order.h"
#include <map>
#include <list>
#include <vector>
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

    OrderBook();

    // Core matching logic
    AddResult addOrder(Order order);

    // Getters for analysis
    bool isBidEmpty() const;
    bool isAskEmpty() const;
    const Bids& getBids() const;
    const Asks& getAsks() const;
    
    // Retrieve executed trades for this session
    const std::vector<Trade>& getTrades() const;

private:
    Bids bids_;
    Asks asks_;
    std::vector<Trade> trades_; // Log of all executions

    // Internal helpers
    void matchAsk(Order& bidOrder);
    void matchBid(Order& askOrder);
};