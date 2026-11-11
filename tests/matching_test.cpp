#include "lob/OrderBook.h"

#include <gtest/gtest.h>

#include <vector>

namespace {

struct RecordingSink : TradeSink {
    std::vector<Trade> trades;
    void onTrade(const Trade& trade) override { trades.push_back(trade); }
};

} // namespace

TEST(Matching, CrossingBuyTradesAtRestingAskPrice) {
    RecordingSink sink;
    OrderBook book(sink);
    book.addOrder({1, Side::Sell, 100, 5});
    const AddResult result = book.addOrder({2, Side::Buy, 105, 3});

    EXPECT_EQ(result.status, AddStatus::Accepted);
    EXPECT_EQ(result.fillCount, 1u);
    EXPECT_EQ(result.filledQuantity, 3u);

    ASSERT_EQ(sink.trades.size(), 1u);
    const Trade& t = sink.trades[0];
    EXPECT_EQ(t.buyOrderId, 2u);
    EXPECT_EQ(t.sellOrderId, 1u);
    EXPECT_EQ(t.price, 100);
    EXPECT_EQ(t.quantity, 3u);
    EXPECT_EQ(book.getAsks().at(100).front().quantity, 2u);
    EXPECT_TRUE(book.isBidEmpty());
}

TEST(Validation, RejectsIdThatIsAlreadyResting) {
    RecordingSink sink;
    OrderBook book(sink);
    book.addOrder({1, Side::Buy, 100, 0});
    book.addOrder({1, Side::Sell, 90, 5});
    const AddResult result = book.addOrder({1, Side::Buy, 95, 5});

    EXPECT_EQ(result.status, AddStatus::Rejected);
    EXPECT_EQ(result.reason, RejectReason::DuplicateId);
    EXPECT_TRUE(sink.trades.empty());
    EXPECT_EQ(book.getAsks().at(90).front().quantity, 5u);
    EXPECT_TRUE(book.isBidEmpty());
}

namespace {

void seedBook(OrderBook& book) {
    book.addOrder({10, Side::Buy, 99, 4});
    book.addOrder({11, Side::Sell, 101, 6});
}

void expectSeededBookUnchanged(const OrderBook& book, const RecordingSink& sink) {
    ASSERT_EQ(book.getBids().size(), 1u);
    ASSERT_EQ(book.getAsks().size(), 1u);
    EXPECT_EQ(book.getBids().at(99).size(), 1u);
    EXPECT_EQ(book.getBids().at(99).front().quantity, 4u);
    EXPECT_EQ(book.getAsks().at(101).size(), 1u);
    EXPECT_EQ(book.getAsks().at(101).front().quantity, 6u);
    EXPECT_TRUE(sink.trades.empty());
}

} // namespace

TEST(Validation, RejectsZeroQuantity) {
    RecordingSink sink;
    OrderBook book(sink);
    seedBook(book);
    const AddResult result = book.addOrder({1, Side::Buy, 101, 0});

    EXPECT_EQ(result.status, AddStatus::Rejected);
    EXPECT_EQ(result.reason, RejectReason::ZeroQuantity);
    expectSeededBookUnchanged(book, sink);
}

TEST(Validation, RejectsZeroPrice) {
    RecordingSink sink;
    OrderBook book(sink);
    seedBook(book);
    const AddResult result = book.addOrder({1, Side::Sell, 0, 3});

    EXPECT_EQ(result.status, AddStatus::Rejected);
    EXPECT_EQ(result.reason, RejectReason::InvalidPrice);
    expectSeededBookUnchanged(book, sink);
}

TEST(Validation, RejectsNegativePrice) {
    RecordingSink sink;
    OrderBook book(sink);
    seedBook(book);
    const AddResult result = book.addOrder({1, Side::Buy, -5, 3});

    EXPECT_EQ(result.status, AddStatus::Rejected);
    EXPECT_EQ(result.reason, RejectReason::InvalidPrice);
    expectSeededBookUnchanged(book, sink);
}

TEST(Validation, DuplicateIdLeavesBookUnchanged) {
    RecordingSink sink;
    OrderBook book(sink);
    seedBook(book);
    const AddResult result = book.addOrder({11, Side::Buy, 101, 6});

    EXPECT_EQ(result.status, AddStatus::Rejected);
    EXPECT_EQ(result.reason, RejectReason::DuplicateId);
    expectSeededBookUnchanged(book, sink);
}
