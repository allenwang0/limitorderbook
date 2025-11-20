#pragma once
#include <cstdint>
#include <iostream>

// Use fixed-point arithmetic for prices to avoid floating-point errors.
// e.g., Price 1050 represents $10.50
using Price = int64_t;
using Quantity = uint64_t;
using OrderId = uint64_t;

enum class Side {
    Buy,
    Sell
};

struct Order {
    OrderId id;
    Side side;
    Price price;
    Quantity quantity;

    // Helper for debugging
    friend std::ostream& operator<<(std::ostream& os, const Order& order) {
        return os << "Order[ID:" << order.id 
                  << " " << (order.side == Side::Buy ? "BUY" : "SELL") 
                  << " @" << order.price 
                  << " Qty:" << order.quantity << "]";
    }
};

struct Trade {
    OrderId buyOrderId;
    OrderId sellOrderId;
    Price price;
    Quantity quantity;
    
    friend std::ostream& operator<<(std::ostream& os, const Trade& trade) {
        return os << "Trade[BuyID:" << trade.buyOrderId 
                  << " SellID:" << trade.sellOrderId 
                  << " @" << trade.price 
                  << " Qty:" << trade.quantity << "]";
    }
};