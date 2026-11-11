#include "lob/OrderBook.h"

#include <gtest/gtest.h>

TEST(Matching, CrossingBuyTradesAtRestingAskPrice) {
    OrderBook book;
    book.addOrder({1, Side::Sell, 100, 5});
    book.addOrder({2, Side::Buy, 105, 3});

    ASSERT_EQ(book.getTrades().size(), 1u);
    const Trade& t = book.getTrades()[0];
    EXPECT_EQ(t.buyOrderId, 2u);
    EXPECT_EQ(t.sellOrderId, 1u);
    EXPECT_EQ(t.price, 100);
    EXPECT_EQ(t.quantity, 3u);
    EXPECT_EQ(book.getAsks().at(100).front().quantity, 2u);
    EXPECT_TRUE(book.isBidEmpty());
}
