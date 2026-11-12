#include "lob/FlatBook.h"

#include <algorithm>

namespace lob {

FlatBook::IdIndex::IdIndex() : slots_(1024, Slot{0, kNone}) {}

std::size_t FlatBook::IdIndex::slotFor(OrderId id) const {
    // Fibonacci hashing: spreads sequential ids across the table.
    return static_cast<std::size_t>(id * 0x9E3779B97F4A7C15ULL) & (slots_.size() - 1);
}

uint32_t FlatBook::IdIndex::find(OrderId id) const {
    for (std::size_t i = slotFor(id);; i = (i + 1) & (slots_.size() - 1)) {
        if (slots_[i].node == kNone) {
            return kNone;
        }
        if (slots_[i].id == id) {
            return slots_[i].node;
        }
    }
}

void FlatBook::IdIndex::insert(OrderId id, uint32_t node) {
    if ((size_ + 1) * 2 > slots_.size()) {
        grow();
    }
    std::size_t i = slotFor(id);
    while (slots_[i].node != kNone) {
        i = (i + 1) & (slots_.size() - 1);
    }
    slots_[i] = {id, node};
    ++size_;
}

void FlatBook::IdIndex::erase(OrderId id) {
    const std::size_t mask = slots_.size() - 1;
    std::size_t hole = slotFor(id);
    while (slots_[hole].id != id || slots_[hole].node == kNone) {
        hole = (hole + 1) & mask;
    }
    for (std::size_t next = (hole + 1) & mask; slots_[next].node != kNone;
         next = (next + 1) & mask) {
        const std::size_t home = slotFor(slots_[next].id);
        const bool movable =
            hole <= next ? (home <= hole || home > next) : (home <= hole && home > next);
        if (movable) {
            slots_[hole] = slots_[next];
            hole = next;
        }
    }
    slots_[hole] = {0, kNone};
    --size_;
}

void FlatBook::IdIndex::grow() {
    std::vector<Slot> old(slots_.size() * 2, Slot{0, kNone});
    old.swap(slots_);
    size_ = 0;
    for (const Slot& slot : old) {
        if (slot.node != kNone) {
            insert(slot.id, slot.node);
        }
    }
}

FlatBook::FlatBook(EventSink& sink, Price basePrice, std::size_t window)
    : sink_(sink), basePrice_(basePrice) {
    bids_.window.resize(window);
    asks_.window.resize(window);
}

void FlatBook::emit(EventType type, RejectReason reason, const Order& order, OrderId restingId,
                    Price price, Quantity quantity) {
    sink_.onEvent({type, reason, order.side, order.id, restingId, price, quantity});
}

bool FlatBook::better(Side side, Price a, Price b) { return side == Side::Buy ? a > b : a < b; }

bool FlatBook::inWindow(Price price) const {
    return price >= basePrice_ && price - basePrice_ < static_cast<Price>(bids_.window.size());
}

FlatBook::Level& FlatBook::levelAt(Side side, Price price) {
    SideBook& sideBook = book(side);
    return inWindow(price) ? sideBook.window[static_cast<std::size_t>(price - basePrice_)]
                           : sideBook.far[price];
}

const FlatBook::Level* FlatBook::findLevel(Side side, Price price) const {
    const SideBook& sideBook = book(side);
    if (inWindow(price)) {
        const Level& level = sideBook.window[static_cast<std::size_t>(price - basePrice_)];
        return level.count == 0 ? nullptr : &level;
    }
    const auto it = sideBook.far.find(price);
    return it == sideBook.far.end() ? nullptr : &it->second;
}

uint32_t FlatBook::allocate() {
    if (!free_.empty()) {
        const uint32_t node = free_.back();
        free_.pop_back();
        return node;
    }
    nodes_.push_back({});
    return static_cast<uint32_t>(nodes_.size() - 1);
}

Result FlatBook::reject(const Order& order, RejectReason reason) {
    emit(EventType::Rejected, reason, order, 0, order.price, order.quantity);
    return {Status::Rejected, reason, 0, 0};
}

Result FlatBook::addOrder(const Order& order) {
    if (order.quantity == 0) {
        return reject(order, RejectReason::ZeroQuantity);
    }
    if (order.type != OrderType::Market && order.price <= 0) {
        return reject(order, RejectReason::InvalidPrice);
    }
    if (index_.find(order.id) != kNone) {
        return reject(order, RejectReason::DuplicateId);
    }
    if (order.type == OrderType::PostOnly) {
        const std::optional<Price> best = book(opposite(order.side)).best;
        if (best && crosses(order, *best)) {
            return reject(order, RejectReason::WouldCross);
        }
    }
    return enter(order);
}

Result FlatBook::enter(const Order& order) {
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

bool FlatBook::crosses(const Order& order, Price restingPrice) const {
    if (order.type == OrderType::Market) {
        return true;
    }
    return order.side == Side::Buy ? order.price >= restingPrice : order.price <= restingPrice;
}

Quantity FlatBook::fillableQuantity(const Order& order) const {
    const Side restingSide = opposite(order.side);
    Quantity available = 0;
    for (std::optional<Price> price = book(restingSide).best;
         price && available < order.quantity && crosses(order, *price);
         price = nextPrice(restingSide, *price)) {
        available += findLevel(restingSide, *price)->quantity;
    }
    return available;
}

Result FlatBook::match(Order& order) {
    Result result{Status::Accepted, RejectReason::None, 0, 0};
    const Side restingSide = opposite(order.side);
    SideBook& opposing = book(restingSide);
    while (order.quantity > 0 && opposing.best && crosses(order, *opposing.best)) {
        const Price levelPrice = *opposing.best;
        Level& level = levelAt(restingSide, levelPrice);
        while (order.quantity > 0 && level.head != kNone) {
            const uint32_t head = level.head;
            Node& resting = nodes_[head];
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
                unlink(head);
            }
        }
        releaseLevelIfEmpty(restingSide, levelPrice);
    }
    return result;
}

void FlatBook::rest(const Order& order) {
    const uint32_t node = allocate();
    Level& level = levelAt(order.side, order.price);
    nodes_[node] = {order.id, order.side, order.price, order.quantity, level.tail, kNone};
    if (level.tail != kNone) {
        nodes_[level.tail].next = node;
    } else {
        level.head = node;
    }
    level.tail = node;
    level.quantity += order.quantity;
    ++level.count;
    SideBook& sideBook = book(order.side);
    ++sideBook.orders;
    if (!sideBook.best || better(order.side, order.price, *sideBook.best)) {
        sideBook.best = order.price;
    }
    index_.insert(order.id, node);
}

void FlatBook::unlink(uint32_t node) {
    const Node& n = nodes_[node];
    Level& level = levelAt(n.side, n.price);
    if (n.prev != kNone) {
        nodes_[n.prev].next = n.next;
    } else {
        level.head = n.next;
    }
    if (n.next != kNone) {
        nodes_[n.next].prev = n.prev;
    } else {
        level.tail = n.prev;
    }
    level.quantity -= n.quantity;
    --level.count;
    --book(n.side).orders;
    free_.push_back(node);
}

void FlatBook::releaseLevelIfEmpty(Side side, Price price) {
    SideBook& sideBook = book(side);
    if (levelAt(side, price).count != 0) {
        return;
    }
    if (!inWindow(price)) {
        sideBook.far.erase(price);
    }
    if (sideBook.best == price) {
        refreshBest(side, price);
    }
}

void FlatBook::refreshBest(Side side, Price emptied) {
    SideBook& sideBook = book(side);
    sideBook.best = sideBook.orders == 0 ? std::nullopt : nextPrice(side, emptied);
}

std::optional<Price> FlatBook::nextPrice(Side side, Price from) const {
    const SideBook& sideBook = book(side);
    std::optional<Price> candidate;
    if (side == Side::Buy) {
        auto it = sideBook.far.lower_bound(from);
        if (it != sideBook.far.begin()) {
            candidate = std::prev(it)->first;
        }
        const Price windowTop = basePrice_ + static_cast<Price>(sideBook.window.size()) - 1;
        for (Price p = std::min(from - 1, windowTop); p >= basePrice_; --p) {
            if (candidate && *candidate >= p) {
                break;
            }
            if (sideBook.window[static_cast<std::size_t>(p - basePrice_)].count != 0) {
                candidate = p;
                break;
            }
        }
    } else {
        const auto it = sideBook.far.upper_bound(from);
        if (it != sideBook.far.end()) {
            candidate = it->first;
        }
        const Price windowTop = basePrice_ + static_cast<Price>(sideBook.window.size()) - 1;
        for (Price p = std::max(from + 1, basePrice_); p <= windowTop; ++p) {
            if (candidate && *candidate <= p) {
                break;
            }
            if (sideBook.window[static_cast<std::size_t>(p - basePrice_)].count != 0) {
                candidate = p;
                break;
            }
        }
    }
    return candidate;
}

Result FlatBook::cancelOrder(OrderId id) {
    const uint32_t node = index_.find(id);
    if (node == kNone) {
        return reject({id, Side::Buy, 0, 0}, RejectReason::UnknownId);
    }
    const Node n = nodes_[node];
    const Order order{id, n.side, n.price, n.quantity};
    index_.erase(id);
    unlink(node);
    releaseLevelIfEmpty(n.side, n.price);
    emit(EventType::Cancelled, RejectReason::None, order, 0, order.price, order.quantity);
    return {Status::Accepted, RejectReason::None, 0, 0};
}

Result FlatBook::modifyOrder(OrderId id, Quantity newQuantity, Price newPrice) {
    const uint32_t node = index_.find(id);
    if (node == kNone) {
        return reject({id, Side::Buy, newPrice, newQuantity}, RejectReason::UnknownId);
    }
    Node& n = nodes_[node];
    const Order modified{id, n.side, newPrice, newQuantity};
    if (newQuantity == 0) {
        return reject(modified, RejectReason::ZeroQuantity);
    }
    if (newPrice <= 0) {
        return reject(modified, RejectReason::InvalidPrice);
    }
    if (newPrice == n.price && newQuantity <= n.quantity) {
        levelAt(n.side, n.price).quantity -= n.quantity - newQuantity;
        n.quantity = newQuantity;
        emit(EventType::Accepted, RejectReason::None, modified, 0, newPrice, newQuantity);
        return {Status::Accepted, RejectReason::None, 0, 0};
    }
    const Side side = n.side;
    const Price oldPrice = n.price;
    index_.erase(id);
    unlink(node);
    releaseLevelIfEmpty(side, oldPrice);
    return enter(modified);
}

std::optional<Price> FlatBook::bestBid() const { return bids_.best; }

std::optional<Price> FlatBook::bestAsk() const { return asks_.best; }

std::optional<Price> FlatBook::spread() const {
    if (!bids_.best || !asks_.best) {
        return std::nullopt;
    }
    return *asks_.best - *bids_.best;
}

std::vector<LevelInfo> FlatBook::depth(Side side, std::size_t levelCount) const {
    std::vector<LevelInfo> out;
    for (std::optional<Price> price = book(side).best; price && out.size() < levelCount;
         price = nextPrice(side, *price)) {
        const Level* level = findLevel(side, *price);
        out.push_back({*price, level->quantity, level->count});
    }
    return out;
}

Quantity FlatBook::volumeAtPrice(Side side, Price price) const {
    const Level* level = findLevel(side, price);
    return level == nullptr ? 0 : level->quantity;
}

bool FlatBook::checkInvariants() const {
    std::size_t total = 0;
    for (const Side side : {Side::Buy, Side::Sell}) {
        const SideBook& sideBook = book(side);
        std::optional<Price> best;
        std::size_t sideOrders = 0;
        const auto checkLevel = [&](Price price, const Level& level) {
            Quantity sum = 0;
            std::size_t count = 0;
            uint32_t prev = kNone;
            for (uint32_t node = level.head; node != kNone; node = nodes_[node].next) {
                const Node& n = nodes_[node];
                if (n.prev != prev || n.side != side || n.price != price || n.quantity == 0 ||
                    index_.find(n.id) != node) {
                    return false;
                }
                sum += n.quantity;
                ++count;
                prev = node;
            }
            if (prev != level.tail || sum != level.quantity || count != level.count) {
                return false;
            }
            if (count != 0 && (!best || better(side, price, *best))) {
                best = price;
            }
            sideOrders += count;
            return true;
        };
        for (std::size_t i = 0; i < sideBook.window.size(); ++i) {
            if (!checkLevel(basePrice_ + static_cast<Price>(i), sideBook.window[i])) {
                return false;
            }
        }
        for (const auto& [price, level] : sideBook.far) {
            if (inWindow(price) || level.count == 0 || !checkLevel(price, level)) {
                return false;
            }
        }
        if (best != sideBook.best || sideOrders != sideBook.orders) {
            return false;
        }
        total += sideOrders;
    }
    const bool crossed = bids_.best && asks_.best && *bids_.best >= *asks_.best;
    return total == index_.size() && !crossed;
}

} // namespace lob
