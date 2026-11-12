#include "lob/MapBook.h"

#include <algorithm>
#include <iterator>

namespace lob {

MapBook::MapBook(EventSink &sink) : sink_(sink) {}

void MapBook::emit(EventType type, RejectReason reason, const Order &order, OrderId restingId,
                   Price price, Quantity quantity) {
    sink_.onEvent({type, reason, order.side, order.id, restingId, price, quantity});
}

Result MapBook::reject(const Order &order, RejectReason reason) {
    emit(EventType::Rejected, reason, order, 0, order.price, order.quantity);
    return {Status::Rejected, reason, 0, 0};
}

Result MapBook::addOrder(const Order &order) {
    if (order.quantity == 0) {
        return reject(order, RejectReason::ZeroQuantity);
    }
    if (order.type != OrderType::Market && order.price <= 0) {
        return reject(order, RejectReason::InvalidPrice);
    }
    if (index_.count(order.id) != 0) {
        return reject(order, RejectReason::DuplicateId);
    }
    if (order.type == OrderType::PostOnly) {
        const Levels &opposing = levels(opposite(order.side));
        if (!opposing.empty() && crosses(order, opposing.begin()->first)) {
            return reject(order, RejectReason::WouldCross);
        }
    }
    return enter(order);
}

Result MapBook::enter(const Order &order) {
    emit(EventType::Accepted, RejectReason::None, order, 0, order.price, order.quantity);
    if (order.type == OrderType::FillOrKill && fillableQuantity(order) < order.quantity) {
        emit(EventType::Cancelled, RejectReason::None, order, 0, order.price, order.quantity);
        return {Status::Accepted, RejectReason::None, 0, 0};
    }
    Order remaining = order;
    const Result result = match(remaining);
    if (remaining.quantity == 0) {
        return result;
    }
    if (order.type == OrderType::Limit || order.type == OrderType::PostOnly) {
        rest(remaining);
    } else {
        emit(EventType::Cancelled, RejectReason::None, order, 0, order.price, remaining.quantity);
    }
    return result;
}

bool MapBook::crosses(const Order &order, Price restingPrice) const {
    if (order.type == OrderType::Market) {
        return true;
    }
    return order.side == Side::Buy ? order.price >= restingPrice : order.price <= restingPrice;
}

Quantity MapBook::fillableQuantity(const Order &order) const {
    Quantity available = 0;
    for (const auto &[price, level] : levels(opposite(order.side))) {
        if (available >= order.quantity || !crosses(order, price)) {
            break;
        }
        available += level.quantity;
    }
    return available;
}

void MapBook::rest(const Order &order) {
    const auto level = levels(order.side).try_emplace(order.price).first;
    level->second.orders.push_back({order.id, order.quantity});
    level->second.quantity += order.quantity;
    index_.emplace(order.id, Locator{order.side, level, std::prev(level->second.orders.end())});
}

void MapBook::remove(const Locator &locator) {
    Level &level = locator.level->second;
    level.quantity -= locator.order->quantity;
    level.orders.erase(locator.order);
    if (level.orders.empty()) {
        levels(locator.side).erase(locator.level);
    }
}

Result MapBook::cancelOrder(OrderId id) {
    const auto it = index_.find(id);
    if (it == index_.end()) {
        return reject({id, Side::Buy, 0, 0}, RejectReason::UnknownId);
    }
    const Locator locator = it->second;
    const Order order{id, locator.side, locator.level->first, locator.order->quantity};
    index_.erase(it);
    remove(locator);
    emit(EventType::Cancelled, RejectReason::None, order, 0, order.price, order.quantity);
    return {Status::Accepted, RejectReason::None, 0, 0};
}

Result MapBook::modifyOrder(OrderId id, Quantity newQuantity, Price newPrice) {
    const auto it = index_.find(id);
    if (it == index_.end()) {
        return reject({id, Side::Buy, newPrice, newQuantity}, RejectReason::UnknownId);
    }
    const Locator locator = it->second;
    const Order modified{id, locator.side, newPrice, newQuantity};
    if (newQuantity == 0) {
        return reject(modified, RejectReason::ZeroQuantity);
    }
    if (newPrice <= 0) {
        return reject(modified, RejectReason::InvalidPrice);
    }
    if (newPrice == locator.level->first && newQuantity <= locator.order->quantity) {
        locator.level->second.quantity -= locator.order->quantity - newQuantity;
        locator.order->quantity = newQuantity;
        emit(EventType::Accepted, RejectReason::None, modified, 0, newPrice, newQuantity);
        return {Status::Accepted, RejectReason::None, 0, 0};
    }
    index_.erase(it);
    remove(locator);
    return enter(modified);
}

Result MapBook::match(Order &order) {
    Result result{Status::Accepted, RejectReason::None, 0, 0};
    Levels &book = levels(opposite(order.side));
    while (order.quantity > 0 && !book.empty()) {
        auto levelIt = book.begin();
        const Price levelPrice = levelIt->first;
        if (!crosses(order, levelPrice)) {
            break;
        }

        Level &level = levelIt->second;
        while (order.quantity > 0 && !level.orders.empty()) {
            Resting &resting = level.orders.front();
            const Quantity quantity = std::min(order.quantity, resting.quantity);
            // Trade at the resting order's price.
            emit(EventType::Fill, RejectReason::None, order, resting.id, levelPrice, quantity);
            order.quantity -= quantity;
            resting.quantity -= quantity;
            level.quantity -= quantity;
            ++result.fillCount;
            result.filledQuantity += quantity;
            if (resting.quantity == 0) {
                index_.erase(resting.id);
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
    for (const auto &[price, level] : levels(side)) {
        if (out.size() == levelCount) {
            break;
        }
        out.push_back({price, level.quantity, level.orders.size()});
    }
    return out;
}

Quantity MapBook::volumeAtPrice(Side side, Price price) const {
    const Levels &book = levels(side);
    const auto it = book.find(price);
    return it == book.end() ? 0 : it->second.quantity;
}

bool MapBook::checkInvariants() const {
    std::size_t orders = 0;
    for (const Side side : {Side::Buy, Side::Sell}) {
        const Levels &book = levels(side);
        for (auto level = book.begin(); level != book.end(); ++level) {
            Quantity sum = 0;
            if (level->second.orders.empty()) {
                return false;
            }
            for (auto order = level->second.orders.begin(); order != level->second.orders.end();
                 ++order) {
                const auto it = index_.find(order->id);
                if (order->quantity == 0 || it == index_.end() || it->second.side != side ||
                    it->second.level != level || it->second.order != order) {
                    return false;
                }
                sum += order->quantity;
                ++orders;
            }
            if (sum != level->second.quantity) {
                return false;
            }
        }
    }
    const bool crossed =
        !bids_.empty() && !asks_.empty() && bids_.begin()->first >= asks_.begin()->first;
    return orders == index_.size() && !crossed;
}

} // namespace lob
