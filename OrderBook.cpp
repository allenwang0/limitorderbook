#include "OrderBook.h"

OrderBook::OrderBook() {
    // Reserve space for trades to minimize reallocation during benchmarking
    trades_.reserve(100000); 
}

void OrderBook::addOrder(Order order) {
    if (order.side == Side::Buy) {
        matchAsk(order);
    } else {
        matchBid(order);
    }
}

// Processing a new BUY order (matches against Ask book)
void OrderBook::matchAsk(Order& bidOrder) {
    while (bidOrder.quantity > 0 && !asks_.empty()) {
        auto bestAskIter = asks_.begin();
        Price bestAskPrice = bestAskIter->first;

        // Check if the buy price crosses the spread
        if (bidOrder.price < bestAskPrice) {
            break; // No match possible, spread not crossed
        }

        // Process orders at this price level (FIFO)
        auto& ordersAtPrice = bestAskIter->second;
        
        while (bidOrder.quantity > 0 && !ordersAtPrice.empty()) {
            Order& askOrder = ordersAtPrice.front();
            
            // Determine trade quantity
            Quantity tradeQty = std::min(bidOrder.quantity, askOrder.quantity);
            
            // Generate Trade
            trades_.push_back({
                bidOrder.id,
                askOrder.id,
                askOrder.price, // Trade happens at the resting order's price
                tradeQty
            });

            // Update quantities
            bidOrder.quantity -= tradeQty;
            askOrder.quantity -= tradeQty;

            // Remove filled orders from book
            if (askOrder.quantity == 0) {
                ordersAtPrice.pop_front();
            }
        }

        // If no more orders at this price level, remove the level
        if (ordersAtPrice.empty()) {
            asks_.erase(bestAskIter);
        }
    }

    // If quantity remains, rest on the Bid book
    if (bidOrder.quantity > 0) {
        bids_[bidOrder.price].push_back(bidOrder);
    }
}

// Processing a new SELL order (matches against Bid book)
void OrderBook::matchBid(Order& askOrder) {
    while (askOrder.quantity > 0 && !bids_.empty()) {
        auto bestBidIter = bids_.begin();
        Price bestBidPrice = bestBidIter->first;

        // Check if sell price crosses spread
        if (askOrder.price > bestBidPrice) {
            break;
        }

        auto& ordersAtPrice = bestBidIter->second;

        while (askOrder.quantity > 0 && !ordersAtPrice.empty()) {
            Order& bidOrder = ordersAtPrice.front();
            
            Quantity tradeQty = std::min(askOrder.quantity, bidOrder.quantity);
            
            trades_.push_back({
                bidOrder.id,
                askOrder.id,
                bidOrder.price,
                tradeQty
            });

            askOrder.quantity -= tradeQty;
            bidOrder.quantity -= tradeQty;

            if (bidOrder.quantity == 0) {
                ordersAtPrice.pop_front();
            }
        }

        if (ordersAtPrice.empty()) {
            bids_.erase(bestBidIter);
        }
    }

    if (askOrder.quantity > 0) {
        asks_[askOrder.price].push_back(askOrder);
    }
}

bool OrderBook::isBidEmpty() const { return bids_.empty(); }
bool OrderBook::isAskEmpty() const { return asks_.empty(); }
const OrderBook::Bids& OrderBook::getBids() const { return bids_; }
const OrderBook::Asks& OrderBook::getAsks() const { return asks_; }
const std::vector<Trade>& OrderBook::getTrades() const { return trades_; }