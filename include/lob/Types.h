#pragma once

#include <cstddef>
#include <cstdint>

namespace lob {

// Prices are integer ticks to avoid floating-point error, e.g. 1050 = $10.50.
using Price = int64_t;
using Quantity = uint64_t;
using OrderId = uint64_t;

enum class Side : uint8_t { Buy, Sell };

constexpr Side opposite(Side side) { return side == Side::Buy ? Side::Sell : Side::Buy; }

struct Order {
    OrderId id;
    Side side;
    Price price;
    Quantity quantity;
};

enum class Status : uint8_t { Accepted, Rejected };

enum class RejectReason : uint8_t { None, ZeroQuantity, InvalidPrice, DuplicateId, UnknownId };

struct Result {
    Status status;
    RejectReason reason;
    std::size_t fillCount;
    Quantity filledQuantity;
};

enum class EventType : uint8_t { Accepted, Fill, Cancelled, Rejected };

// Fill: orderId is the incoming order, restingId the order it traded with,
// side the incoming order's side and price the resting order's price.
// Cancelled: quantity is the open quantity removed from the book.
struct Event {
    EventType type;
    RejectReason reason;
    Side side;
    OrderId orderId;
    OrderId restingId;
    Price price;
    Quantity quantity;

    friend bool operator==(const Event& a, const Event& b) {
        return a.type == b.type && a.reason == b.reason && a.side == b.side &&
               a.orderId == b.orderId && a.restingId == b.restingId && a.price == b.price &&
               a.quantity == b.quantity;
    }
    friend bool operator!=(const Event& a, const Event& b) { return !(a == b); }
};

class EventSink {
public:
    virtual ~EventSink() = default;
    virtual void onEvent(const Event& event) = 0;
};

struct LevelInfo {
    Price price;
    Quantity quantity;
    std::size_t orderCount;

    friend bool operator==(const LevelInfo& a, const LevelInfo& b) {
        return a.price == b.price && a.quantity == b.quantity && a.orderCount == b.orderCount;
    }
    friend bool operator!=(const LevelInfo& a, const LevelInfo& b) { return !(a == b); }
};

} // namespace lob
