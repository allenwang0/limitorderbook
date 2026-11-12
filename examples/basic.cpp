#include "lob/MapBook.h"

#include <iostream>

using namespace lob;

struct PrintFills : EventSink {
    void onEvent(const Event& event) override {
        if (event.type == EventType::Fill) {
            std::cout << "fill: order " << event.orderId << " bought " << event.quantity << " @ "
                      << event.price << " from order " << event.restingId << '\n';
        }
    }
};

int main() {
    PrintFills sink;
    MapBook book(sink);

    book.addOrder({1, Side::Sell, 10'050, 100});
    book.addOrder({2, Side::Sell, 10'060, 200});
    book.addOrder({3, Side::Buy, 10'000, 150});
    book.addOrder({4, Side::Buy, 10'055, 120});
    book.cancelOrder(3);

    for (const LevelInfo& level : book.depth(Side::Sell, 5)) {
        std::cout << "ask " << level.price << ": " << level.quantity << " in " << level.orderCount
                  << " order(s)\n";
    }
    std::cout << "best bid: " << book.bestBid().value_or(0) << '\n';
}
