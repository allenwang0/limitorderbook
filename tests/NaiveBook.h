#pragma once

#include "lob/Types.h"

#include <algorithm>
#include <cstddef>
#include <map>
#include <optional>
#include <vector>

namespace lob::test {

// Reference book for the differential test: one vector, linear scans, no
// index. Slow on purpose so its correctness is easy to check by reading.
class NaiveBook {
public:
    static constexpr const char *name = "NaiveBook";

    explicit NaiveBook(EventSink &sink) : sink_(sink) {}

    Result addOrder(const Order &order) {
        if (order.quantity == 0) {
            return reject(order, RejectReason::ZeroQuantity);
        }
        if (order.type != OrderType::Market && order.price <= 0) {
            return reject(order, RejectReason::InvalidPrice);
        }
        if (find(order.id) != orders_.end()) {
            return reject(order, RejectReason::DuplicateId);
        }
        if (order.type == OrderType::PostOnly) {
            const auto best = bestOpposite(order.side);
            if (best != orders_.end() && crosses(order, best->price)) {
                return reject(order, RejectReason::WouldCross);
            }
        }
        return enter(order);
    }

    Result cancelOrder(OrderId id) {
        const auto it = find(id);
        if (it == orders_.end()) {
            return reject({id, Side::Buy, 0, 0}, RejectReason::UnknownId);
        }
        const Resting resting = *it;
        orders_.erase(it);
        emit(EventType::Cancelled, {id, resting.side, resting.price, resting.quantity}, 0,
             resting.price, resting.quantity);
        return {Status::Accepted, RejectReason::None, 0, 0};
    }

    Result modifyOrder(OrderId id, Quantity newQuantity, Price newPrice) {
        const auto it = find(id);
        if (it == orders_.end()) {
            return reject({id, Side::Buy, newPrice, newQuantity}, RejectReason::UnknownId);
        }
        const Order modified{id, it->side, newPrice, newQuantity};
        if (newQuantity == 0) {
            return reject(modified, RejectReason::ZeroQuantity);
        }
        if (newPrice <= 0) {
            return reject(modified, RejectReason::InvalidPrice);
        }
        if (newPrice == it->price && newQuantity <= it->quantity) {
            it->quantity = newQuantity;
            emit(EventType::Accepted, modified, 0, newPrice, newQuantity);
            return {Status::Accepted, RejectReason::None, 0, 0};
        }
        orders_.erase(it);
        return enter(modified);
    }

    std::optional<Price> bestBid() const { return bestPrice(Side::Buy); }
    std::optional<Price> bestAsk() const { return bestPrice(Side::Sell); }

    std::vector<LevelInfo> depth(Side side, std::size_t levelCount) const {
        std::map<Price, LevelInfo> byPrice;
        for (const Resting &resting : orders_) {
            if (resting.side == side) {
                LevelInfo &level = byPrice[resting.price];
                level.price = resting.price;
                level.quantity += resting.quantity;
                ++level.orderCount;
            }
        }
        std::vector<LevelInfo> out;
        for (const auto &entry : byPrice) {
            out.push_back(entry.second);
        }
        if (side == Side::Buy) {
            std::reverse(out.begin(), out.end());
        }
        out.resize(std::min(out.size(), levelCount));
        return out;
    }

    std::size_t orderCount() const { return orders_.size(); }

    std::vector<OrderId> restingIds() const {
        std::vector<OrderId> ids;
        for (const Resting &resting : orders_) {
            ids.push_back(resting.id);
        }
        return ids;
    }

private:
    struct Resting {
        OrderId id;
        Side side;
        Price price;
        Quantity quantity;
        std::size_t sequence;
    };

    using Orders = std::vector<Resting>;

    void emit(EventType type, const Order &order, OrderId restingId, Price price, Quantity quantity,
              RejectReason reason = RejectReason::None) {
        sink_.onEvent({type, reason, order.side, order.id, restingId, price, quantity});
    }

    Result reject(const Order &order, RejectReason reason) {
        emit(EventType::Rejected, order, 0, order.price, order.quantity, reason);
        return {Status::Rejected, reason, 0, 0};
    }

    Orders::iterator find(OrderId id) {
        return std::find_if(orders_.begin(), orders_.end(),
                            [id](const Resting &resting) { return resting.id == id; });
    }

    static bool better(const Resting &a, const Resting &b) {
        if (a.price != b.price) {
            return a.side == Side::Buy ? a.price > b.price : a.price < b.price;
        }
        return a.sequence < b.sequence;
    }

    Orders::iterator bestOpposite(Side side) {
        auto best = orders_.end();
        for (auto it = orders_.begin(); it != orders_.end(); ++it) {
            if (it->side != side && (best == orders_.end() || better(*it, *best))) {
                best = it;
            }
        }
        return best;
    }

    std::optional<Price> bestPrice(Side side) const {
        std::optional<Price> best;
        for (const Resting &resting : orders_) {
            if (resting.side == side &&
                (!best || (side == Side::Buy ? resting.price > *best : resting.price < *best))) {
                best = resting.price;
            }
        }
        return best;
    }

    static bool crosses(const Order &order, Price restingPrice) {
        if (order.type == OrderType::Market) {
            return true;
        }
        return order.side == Side::Buy ? order.price >= restingPrice : order.price <= restingPrice;
    }

    Result enter(const Order &order) {
        emit(EventType::Accepted, order, 0, order.price, order.quantity);
        Result result{Status::Accepted, RejectReason::None, 0, 0};
        if (order.type == OrderType::FillOrKill) {
            Quantity available = 0;
            for (const Resting &resting : orders_) {
                if (resting.side != order.side && crosses(order, resting.price)) {
                    available += resting.quantity;
                }
            }
            if (available < order.quantity) {
                emit(EventType::Cancelled, order, 0, order.price, order.quantity);
                return result;
            }
        }
        Quantity remaining = order.quantity;
        while (remaining > 0) {
            const auto best = bestOpposite(order.side);
            if (best == orders_.end() || !crosses(order, best->price)) {
                break;
            }
            const Quantity quantity = std::min(remaining, best->quantity);
            emit(EventType::Fill, order, best->id, best->price, quantity);
            remaining -= quantity;
            best->quantity -= quantity;
            ++result.fillCount;
            result.filledQuantity += quantity;
            if (best->quantity == 0) {
                orders_.erase(best);
            }
        }
        if (remaining > 0) {
            if (order.type == OrderType::Limit || order.type == OrderType::PostOnly) {
                orders_.push_back({order.id, order.side, order.price, remaining, nextSequence_++});
            } else {
                emit(EventType::Cancelled, order, 0, order.price, remaining);
            }
        }
        return result;
    }

    EventSink &sink_;
    Orders orders_;
    std::size_t nextSequence_ = 0;
};

} // namespace lob::test
