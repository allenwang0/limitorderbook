#include "lob/OrderBook.h"

#include <gtest/gtest.h>

TEST(Matching, CrossingBuyTradesAtRestingAskPrice) {
    OrderBook book;
    book.addOrder({1, Side::Sell, 100, 5});
    const AddResult result = book.addOrder({2, Side::Buy, 105, 3});

    EXPECT_EQ(result.status, AddStatus::Accepted);
    EXPECT_EQ(result.fillCount, 1u);
    EXPECT_EQ(result.filledQuantity, 3u);

    ASSERT_EQ(book.getTrades().size(), 1u);
    const Trade& t = book.getTrades()[0];
    EXPECT_EQ(t.buyOrderId, 2u);
    EXPECT_EQ(t.sellOrderId, 1u);
    EXPECT_EQ(t.price, 100);
    EXPECT_EQ(t.quantity, 3u);
    EXPECT_EQ(book.getAsks().at(100).front().quantity, 2u);
    EXPECT_TRUE(book.isBidEmpty());
}

TEST(Validation, RejectsIdThatIsAlreadyResting) {
    OrderBook book;
    book.addOrder({1, Side::Buy, 100, 0});
    book.addOrder({1, Side::Sell, 90, 5});
    const AddResult result = book.addOrder({1, Side::Buy, 95, 5});

    EXPECT_EQ(result.status, AddStatus::Rejected);
    EXPECT_EQ(result.reason, RejectReason::DuplicateId);
    EXPECT_TRUE(book.getTrades().empty());
    EXPECT_EQ(book.getAsks().at(90).front().quantity, 5u);
    EXPECT_TRUE(book.isBidEmpty());
}
