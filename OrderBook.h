#pragma once
#include "Order.h"
#include <map>
#include <list>
#include <vector>
#include <functional>

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
    void addOrder(Order order);

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