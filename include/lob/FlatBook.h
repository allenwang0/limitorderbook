#pragma once

#include "lob/Types.h"

#include <cstddef>
#include <cstdint>
#include <map>
#include <optional>
#include <vector>

namespace lob {

// Orders live in a pooled array and link to each other by index, so adding
// an order after warm-up does no heap allocation. Price levels near
// basePrice sit in a flat array indexed by tick offset; prices outside the
// window fall back to a std::map. The best price on each side is cached.
class FlatBook {
public:
    static constexpr const char* name = "FlatBook";

    // Window of 4096 ticks starting 2048 below $100.00 (10000 ticks), the
    // centre of the synthetic flows in the tests and benchmark.
    static constexpr Price kDefaultBasePrice = 10'000 - 2'048;
    static constexpr std::size_t kDefaultWindow = 4'096;

    explicit FlatBook(EventSink& sink, Price basePrice = kDefaultBasePrice,
                      std::size_t window = kDefaultWindow);

    Result addOrder(const Order& order);
    Result cancelOrder(OrderId id);
    // Same rule as MapBook: a size reduction at the same price keeps queue
    // position; anything else cancels and re-adds as a Limit order.
    Result modifyOrder(OrderId id, Quantity newQuantity, Price newPrice);

    std::optional<Price> bestBid() const;
    std::optional<Price> bestAsk() const;
    std::optional<Price> spread() const;
    std::vector<LevelInfo> depth(Side side, std::size_t levels) const;
    Quantity volumeAtPrice(Side side, Price price) const;
    std::size_t orderCount() const { return index_.size(); }
    bool checkInvariants() const;

private:
    static constexpr uint32_t kNone = UINT32_MAX;

    struct Node {
        OrderId id;
        Side side;
        Price price;
        Quantity quantity;
        uint32_t prev;
        uint32_t next;
    };

    struct Level {
        uint32_t head = kNone;
        uint32_t tail = kNone;
        Quantity quantity = 0;
        std::size_t count = 0;
    };

    struct SideBook {
        std::vector<Level> window;
        std::map<Price, Level> far;
        std::optional<Price> best;
        std::size_t orders = 0;
    };

    // Open addressing with linear probing and backward-shift deletion, so
    // the id index allocates only when it grows.
    class IdIndex {
    public:
        IdIndex();
        uint32_t find(OrderId id) const;
        void insert(OrderId id, uint32_t node);
        void erase(OrderId id);
        std::size_t size() const { return size_; }

    private:
        struct Slot {
            OrderId id;
            uint32_t node;
        };
        std::size_t slotFor(OrderId id) const;
        void grow();

        std::vector<Slot> slots_;
        std::size_t size_ = 0;
    };

    SideBook& book(Side side) { return side == Side::Buy ? bids_ : asks_; }
    const SideBook& book(Side side) const { return side == Side::Buy ? bids_ : asks_; }
    bool inWindow(Price price) const;
    Level& levelAt(Side side, Price price);
    const Level* findLevel(Side side, Price price) const;
    static bool better(Side side, Price a, Price b);

    Result reject(const Order& order, RejectReason reason);
    Result enter(const Order& order);
    bool crosses(const Order& order, Price restingPrice) const;
    Quantity fillableQuantity(const Order& order) const;
    Result match(Order& order);
    void rest(const Order& order);
    void unlink(uint32_t node);
    void releaseLevelIfEmpty(Side side, Price price);
    void refreshBest(Side side, Price emptied);
    std::optional<Price> nextPrice(Side side, Price from) const;
    uint32_t allocate();
    void emit(EventType type, RejectReason reason, const Order& order, OrderId restingId,
              Price price, Quantity quantity);

    EventSink& sink_;
    Price basePrice_;
    std::vector<Node> nodes_;
    std::vector<uint32_t> free_;
    IdIndex index_;
    SideBook bids_;
    SideBook asks_;
};

} // namespace lob
