#include "lob/OrderBook.h"

OrderBook::OrderBook(TradeSink& sink) : sink_(sink) {}

AddResult OrderBook::addOrder(Order order) {
    if (order.quantity == 0) {
        return {AddStatus::Rejected, RejectReason::ZeroQuantity, 0, 0};
    }
    if (order.price <= 0) {
        return {AddStatus::Rejected, RejectReason::InvalidPrice, 0, 0};
    }
    if (restingIds_.count(order.id) != 0) {
        return {AddStatus::Rejected, RejectReason::DuplicateId, 0, 0};
    }
    const Quantity requested = order.quantity;
    const std::size_t fills = order.side == Side::Buy ? matchAsk(order) : matchBid(order);
    return {AddStatus::Accepted, RejectReason::None, fills, requested - order.quantity};
}

// Processing a new BUY order (matches against Ask book)
std::size_t OrderBook::matchAsk(Order& bidOrder) {
    std::size_t fills = 0;
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
            sink_.onTrade({
                bidOrder.id,
                askOrder.id,
                askOrder.price, // Trade happens at the resting order's price
                tradeQty
            });
            ++fills;

            // Update quantities
            bidOrder.quantity -= tradeQty;
            askOrder.quantity -= tradeQty;

            // Remove filled orders from book
            if (askOrder.quantity == 0) {
                restingIds_.erase(askOrder.id);
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
        restingIds_.insert(bidOrder.id);
    }
    return fills;
}

// Processing a new SELL order (matches against Bid book)
std::size_t OrderBook::matchBid(Order& askOrder) {
    std::size_t fills = 0;
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
            
            sink_.onTrade({
                bidOrder.id,
                askOrder.id,
                bidOrder.price,
                tradeQty
            });
            ++fills;

            askOrder.quantity -= tradeQty;
            bidOrder.quantity -= tradeQty;

            if (bidOrder.quantity == 0) {
                restingIds_.erase(bidOrder.id);
                ordersAtPrice.pop_front();
            }
        }

        if (ordersAtPrice.empty()) {
            bids_.erase(bestBidIter);
        }
    }

    if (askOrder.quantity > 0) {
        asks_[askOrder.price].push_back(askOrder);
        restingIds_.insert(askOrder.id);
    }
    return fills;
}

bool OrderBook::isBidEmpty() const { return bids_.empty(); }
bool OrderBook::isAskEmpty() const { return asks_.empty(); }
const OrderBook::Bids& OrderBook::getBids() const { return bids_; }
const OrderBook::Asks& OrderBook::getAsks() const { return asks_; }
