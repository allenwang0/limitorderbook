#include "lob/MapBook.h"

#include <algorithm>

namespace lob {

MapBook::MapBook(EventSink& sink) : sink_(sink) {}

void MapBook::emit(EventType type, RejectReason reason, const Order& order, OrderId restingId,
                   Price price, Quantity quantity) {
    sink_.onEvent({type, reason, order.side, order.id, restingId, price, quantity});
}

Result MapBook::addOrder(const Order& order) {
    RejectReason reason = RejectReason::None;
    if (order.quantity == 0) {
        reason = RejectReason::ZeroQuantity;
    } else if (order.price <= 0) {
        reason = RejectReason::InvalidPrice;
    } else if (restingIds_.count(order.id) != 0) {
        reason = RejectReason::DuplicateId;
    }
    if (reason != RejectReason::None) {
        emit(EventType::Rejected, reason, order, 0, order.price, order.quantity);
        return {Status::Rejected, reason, 0, 0};
    }

    emit(EventType::Accepted, RejectReason::None, order, 0, order.price, order.quantity);
    Order remaining = order;
    const Result result = match(remaining);
    if (remaining.quantity > 0) {
        Level& level = levels(order.side)[order.price];
        level.orders.push_back({order.id, remaining.quantity});
        level.quantity += remaining.quantity;
        restingIds_.insert(order.id);
    }
    return result;
}

Result MapBook::match(Order& order) {
    Result result{Status::Accepted, RejectReason::None, 0, 0};
    Levels& book = levels(opposite(order.side));
    while (order.quantity > 0 && !book.empty()) {
        auto levelIt = book.begin();
        const Price levelPrice = levelIt->first;
        const bool crosses =
            order.side == Side::Buy ? order.price >= levelPrice : order.price <= levelPrice;
        if (!crosses) {
            break;
        }

        Level& level = levelIt->second;
        while (order.quantity > 0 && !level.orders.empty()) {
            Resting& resting = level.orders.front();
            const Quantity quantity = std::min(order.quantity, resting.quantity);
            // Trade at the resting order's price.
            emit(EventType::Fill, RejectReason::None, order, resting.id, levelPrice, quantity);
            order.quantity -= quantity;
            resting.quantity -= quantity;
            level.quantity -= quantity;
            ++result.fillCount;
            result.filledQuantity += quantity;
            if (resting.quantity == 0) {
                restingIds_.erase(resting.id);
                level.orders.pop_front();
            }
        }
        if (level.orders.empty()) {
            book.erase(levelIt);
        }
    }
    return result;
}

std::optional<Price> MapBook::bestBid() const {
    return bids_.empty() ? std::nullopt : std::optional<Price>(bids_.begin()->first);
}

std::optional<Price> MapBook::bestAsk() const {
    return asks_.empty() ? std::nullopt : std::optional<Price>(asks_.begin()->first);
}

std::optional<Price> MapBook::spread() const {
    if (bids_.empty() || asks_.empty()) {
        return std::nullopt;
    }
    return asks_.begin()->first - bids_.begin()->first;
}

std::vector<LevelInfo> MapBook::depth(Side side, std::size_t levelCount) const {
    std::vector<LevelInfo> out;
    for (const auto& [price, level] : levels(side)) {
        if (out.size() == levelCount) {
            break;
        }
        out.push_back({price, level.quantity, level.orders.size()});
    }
    return out;
}

Quantity MapBook::volumeAtPrice(Side side, Price price) const {
    const Levels& book = levels(side);
    const auto it = book.find(price);
    return it == book.end() ? 0 : it->second.quantity;
}

} // namespace lob
