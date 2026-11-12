#include "RecordingSink.h"
#include "lob/FlatBook.h"
#include "lob/MapBook.h"

#include <gtest/gtest.h>

#include <string>
#include <vector>

namespace lob::test {
namespace {

template <class Book> class BookTest : public ::testing::Test {
protected:
    RecordingSink sink;
    Book book{sink};

    std::vector<Event> fills() const { return sink.ofType(EventType::Fill); }

    void seed() {
        book.addOrder({10, Side::Buy, 99, 4});
        book.addOrder({11, Side::Sell, 101, 6});
        sink.events.clear();
    }

    void expectSeededBookUnchanged() const {
        EXPECT_EQ(book.depth(Side::Buy, 10), (std::vector<LevelInfo>{{99, 4, 1}}));
        EXPECT_EQ(book.depth(Side::Sell, 10), (std::vector<LevelInfo>{{101, 6, 1}}));
        EXPECT_TRUE(fills().empty());
    }
};

struct BookName {
    template <class Book> static std::string GetName(int) { return Book::name; }
};

using Books = ::testing::Types<MapBook, FlatBook>;
TYPED_TEST_SUITE(BookTest, Books, BookName);

TYPED_TEST(BookTest, CrossingBuyTradesAtRestingAskPrice) {
    this->book.addOrder({1, Side::Sell, 100, 5});
    const Result result = this->book.addOrder({2, Side::Buy, 105, 3});

    EXPECT_EQ(result.status, Status::Accepted);
    EXPECT_EQ(result.fillCount, 1u);
    EXPECT_EQ(result.filledQuantity, 3u);
    ASSERT_EQ(this->fills().size(), 1u);
    const Event fill = this->fills()[0];
    EXPECT_EQ(fill.orderId, 2u);
    EXPECT_EQ(fill.restingId, 1u);
    EXPECT_EQ(fill.price, 100);
    EXPECT_EQ(fill.quantity, 3u);
    EXPECT_EQ(this->book.depth(Side::Sell, 10), (std::vector<LevelInfo>{{100, 2, 1}}));
    EXPECT_FALSE(this->book.bestBid().has_value());
}

TYPED_TEST(BookTest, RejectsIdThatIsAlreadyResting) {
    this->book.addOrder({1, Side::Buy, 100, 0});
    this->book.addOrder({1, Side::Sell, 90, 5});
    const Result result = this->book.addOrder({1, Side::Buy, 95, 5});

    EXPECT_EQ(result.status, Status::Rejected);
    EXPECT_EQ(result.reason, RejectReason::DuplicateId);
    EXPECT_TRUE(this->fills().empty());
    EXPECT_EQ(this->book.depth(Side::Sell, 10), (std::vector<LevelInfo>{{90, 5, 1}}));
    EXPECT_FALSE(this->book.bestBid().has_value());
}

TYPED_TEST(BookTest, RejectsZeroQuantity) {
    this->seed();
    const Result result = this->book.addOrder({1, Side::Buy, 101, 0});

    EXPECT_EQ(result.reason, RejectReason::ZeroQuantity);
    this->expectSeededBookUnchanged();
}

TYPED_TEST(BookTest, RejectsZeroPrice) {
    this->seed();
    const Result result = this->book.addOrder({1, Side::Sell, 0, 3});

    EXPECT_EQ(result.reason, RejectReason::InvalidPrice);
    this->expectSeededBookUnchanged();
}

TYPED_TEST(BookTest, RejectsNegativePrice) {
    this->seed();
    const Result result = this->book.addOrder({1, Side::Buy, -5, 3});

    EXPECT_EQ(result.reason, RejectReason::InvalidPrice);
    this->expectSeededBookUnchanged();
}

TYPED_TEST(BookTest, DuplicateIdLeavesBookUnchanged) {
    this->seed();
    const Result result = this->book.addOrder({11, Side::Buy, 101, 6});

    EXPECT_EQ(result.status, Status::Rejected);
    EXPECT_EQ(result.reason, RejectReason::DuplicateId);
    this->expectSeededBookUnchanged();
}

TYPED_TEST(BookTest, RejectionEmitsOnlyARejectedEvent) {
    this->seed();
    this->book.addOrder({1, Side::Buy, 101, 0});

    ASSERT_EQ(this->sink.events.size(), 1u);
    EXPECT_EQ(this->sink.events[0].type, EventType::Rejected);
    EXPECT_EQ(this->sink.events[0].reason, RejectReason::ZeroQuantity);
}

TYPED_TEST(BookTest, AsksAtOnePriceFillInArrivalOrder) {
    this->book.addOrder({1, Side::Sell, 100, 2});
    this->book.addOrder({2, Side::Sell, 100, 2});
    this->book.addOrder({3, Side::Buy, 100, 3});

    const auto fills = this->fills();
    ASSERT_EQ(fills.size(), 2u);
    EXPECT_EQ(fills[0].restingId, 1u);
    EXPECT_EQ(fills[0].quantity, 2u);
    EXPECT_EQ(fills[1].restingId, 2u);
    EXPECT_EQ(fills[1].quantity, 1u);
}

TYPED_TEST(BookTest, PartialFillKeepsRemainderPriority) {
    this->book.addOrder({1, Side::Sell, 100, 5});
    this->book.addOrder({2, Side::Sell, 100, 5});
    this->book.addOrder({3, Side::Buy, 100, 2});
    this->sink.events.clear();
    this->book.addOrder({4, Side::Buy, 100, 4});

    const auto fills = this->fills();
    ASSERT_EQ(fills.size(), 2u);
    EXPECT_EQ(fills[0].restingId, 1u);
    EXPECT_EQ(fills[0].quantity, 3u);
    EXPECT_EQ(fills[1].restingId, 2u);
    EXPECT_EQ(fills[1].quantity, 1u);
}

TYPED_TEST(BookTest, SweepFillsEachLevelAtItsRestingPrice) {
    this->book.addOrder({1, Side::Sell, 100, 1});
    this->book.addOrder({2, Side::Sell, 101, 1});
    this->book.addOrder({3, Side::Sell, 102, 1});
    this->book.addOrder({4, Side::Sell, 103, 1});
    const Result result = this->book.addOrder({5, Side::Buy, 102, 5});

    const auto fills = this->fills();
    ASSERT_EQ(fills.size(), 3u);
    EXPECT_EQ(fills[0].price, 100);
    EXPECT_EQ(fills[1].price, 101);
    EXPECT_EQ(fills[2].price, 102);
    EXPECT_EQ(result.filledQuantity, 3u);
    EXPECT_EQ(this->book.bestBid(), 102);
    EXPECT_EQ(this->book.volumeAtPrice(Side::Buy, 102), 2u);
    EXPECT_EQ(this->book.bestAsk(), 103);
}

TYPED_TEST(BookTest, QueriesReportAggregatesPerLevel) {
    this->book.addOrder({1, Side::Buy, 98, 3});
    this->book.addOrder({2, Side::Buy, 99, 4});
    this->book.addOrder({3, Side::Buy, 99, 5});
    this->book.addOrder({4, Side::Sell, 102, 7});

    EXPECT_EQ(this->book.bestBid(), 99);
    EXPECT_EQ(this->book.bestAsk(), 102);
    EXPECT_EQ(this->book.spread(), 3);
    EXPECT_EQ(this->book.depth(Side::Buy, 10), (std::vector<LevelInfo>{{99, 9, 2}, {98, 3, 1}}));
    EXPECT_EQ(this->book.depth(Side::Buy, 1), (std::vector<LevelInfo>{{99, 9, 2}}));
    EXPECT_EQ(this->book.volumeAtPrice(Side::Buy, 99), 9u);
    EXPECT_EQ(this->book.volumeAtPrice(Side::Sell, 99), 0u);
}

TYPED_TEST(BookTest, EmptyBookHasNoTouch) {
    EXPECT_FALSE(this->book.bestBid().has_value());
    EXPECT_FALSE(this->book.bestAsk().has_value());
    EXPECT_FALSE(this->book.spread().has_value());
    EXPECT_TRUE(this->book.depth(Side::Buy, 10).empty());
}

TYPED_TEST(BookTest, CancelRemovesOrderAndReportsOpenQuantity) {
    this->book.addOrder({1, Side::Buy, 99, 5});
    this->book.addOrder({2, Side::Buy, 99, 3});
    this->sink.events.clear();
    const Result result = this->book.cancelOrder(1);

    EXPECT_EQ(result.status, Status::Accepted);
    ASSERT_EQ(this->sink.events.size(), 1u);
    EXPECT_EQ(this->sink.events[0].type, EventType::Cancelled);
    EXPECT_EQ(this->sink.events[0].price, 99);
    EXPECT_EQ(this->sink.events[0].quantity, 5u);
    EXPECT_EQ(this->book.depth(Side::Buy, 10), (std::vector<LevelInfo>{{99, 3, 1}}));
    EXPECT_TRUE(this->book.checkInvariants());
}

TYPED_TEST(BookTest, CancelOfLastOrderRemovesLevel) {
    this->book.addOrder({1, Side::Sell, 101, 5});
    this->book.addOrder({2, Side::Sell, 102, 5});
    this->book.cancelOrder(1);

    EXPECT_EQ(this->book.bestAsk(), 102);
    EXPECT_EQ(this->book.volumeAtPrice(Side::Sell, 101), 0u);
    EXPECT_EQ(this->book.orderCount(), 1u);
}

TYPED_TEST(BookTest, CancelOfUnknownIdIsRejected) {
    this->book.addOrder({1, Side::Buy, 99, 5});
    this->sink.events.clear();
    const Result result = this->book.cancelOrder(42);

    EXPECT_EQ(result.status, Status::Rejected);
    EXPECT_EQ(result.reason, RejectReason::UnknownId);
    ASSERT_EQ(this->sink.events.size(), 1u);
    EXPECT_EQ(this->sink.events[0].type, EventType::Rejected);
    EXPECT_EQ(this->book.orderCount(), 1u);
}

TYPED_TEST(BookTest, CancelledIdCanBeReused) {
    this->book.addOrder({1, Side::Buy, 99, 5});
    this->book.cancelOrder(1);

    EXPECT_EQ(this->book.addOrder({1, Side::Buy, 98, 2}).status, Status::Accepted);
}

TYPED_TEST(BookTest, SizeReductionKeepsQueuePosition) {
    this->book.addOrder({1, Side::Sell, 100, 5});
    this->book.addOrder({2, Side::Sell, 100, 5});
    const Result result = this->book.modifyOrder(1, 2, 100);
    this->sink.events.clear();
    this->book.addOrder({3, Side::Buy, 100, 1});

    EXPECT_EQ(result.status, Status::Accepted);
    ASSERT_EQ(this->fills().size(), 1u);
    EXPECT_EQ(this->fills()[0].restingId, 1u);
    EXPECT_EQ(this->book.volumeAtPrice(Side::Sell, 100), 6u);
}

TYPED_TEST(BookTest, SizeIncreaseLosesQueuePosition) {
    this->book.addOrder({1, Side::Sell, 100, 5});
    this->book.addOrder({2, Side::Sell, 100, 5});
    this->book.modifyOrder(1, 8, 100);
    this->sink.events.clear();
    this->book.addOrder({3, Side::Buy, 100, 1});

    ASSERT_EQ(this->fills().size(), 1u);
    EXPECT_EQ(this->fills()[0].restingId, 2u);
    EXPECT_EQ(this->book.volumeAtPrice(Side::Sell, 100), 12u);
}

TYPED_TEST(BookTest, PriceChangeLosesQueuePositionAndCanTrade) {
    this->book.addOrder({1, Side::Buy, 99, 5});
    this->book.addOrder({2, Side::Sell, 101, 3});
    this->sink.events.clear();
    const Result result = this->book.modifyOrder(1, 5, 101);

    EXPECT_EQ(result.filledQuantity, 3u);
    ASSERT_EQ(this->fills().size(), 1u);
    EXPECT_EQ(this->fills()[0].orderId, 1u);
    EXPECT_EQ(this->fills()[0].price, 101);
    EXPECT_EQ(this->book.depth(Side::Buy, 10), (std::vector<LevelInfo>{{101, 2, 1}}));
    EXPECT_FALSE(this->book.bestAsk().has_value());
}

TYPED_TEST(BookTest, ModifyRejectsUnknownIdAndBadValues) {
    this->book.addOrder({1, Side::Buy, 99, 5});

    EXPECT_EQ(this->book.modifyOrder(2, 5, 99).reason, RejectReason::UnknownId);
    EXPECT_EQ(this->book.modifyOrder(1, 0, 99).reason, RejectReason::ZeroQuantity);
    EXPECT_EQ(this->book.modifyOrder(1, 5, 0).reason, RejectReason::InvalidPrice);
    EXPECT_EQ(this->book.depth(Side::Buy, 10), (std::vector<LevelInfo>{{99, 5, 1}}));
}

TYPED_TEST(BookTest, MarketOrderSweepsAndNeverRests) {
    this->book.addOrder({1, Side::Sell, 100, 2});
    this->book.addOrder({2, Side::Sell, 150, 2});
    this->sink.events.clear();
    const Result result = this->book.addOrder({3, Side::Buy, 0, 5, OrderType::Market});

    EXPECT_EQ(result.status, Status::Accepted);
    EXPECT_EQ(result.filledQuantity, 4u);
    const auto cancels = this->sink.ofType(EventType::Cancelled);
    ASSERT_EQ(cancels.size(), 1u);
    EXPECT_EQ(cancels[0].quantity, 1u);
    EXPECT_FALSE(this->book.bestBid().has_value());
    EXPECT_FALSE(this->book.bestAsk().has_value());
}

TYPED_TEST(BookTest, MarketOrderOnEmptyBookIsCancelled) {
    this->book.addOrder({1, Side::Sell, 0, 5, OrderType::Market});

    ASSERT_EQ(this->sink.events.size(), 2u);
    EXPECT_EQ(this->sink.events[0].type, EventType::Accepted);
    EXPECT_EQ(this->sink.events[1].type, EventType::Cancelled);
    EXPECT_EQ(this->book.orderCount(), 0u);
}

TYPED_TEST(BookTest, ImmediateOrCancelStopsAtLimitAndCancelsRest) {
    this->book.addOrder({1, Side::Buy, 100, 2});
    this->book.addOrder({2, Side::Buy, 98, 2});
    const Result result = this->book.addOrder({3, Side::Sell, 99, 5, OrderType::ImmediateOrCancel});

    EXPECT_EQ(result.filledQuantity, 2u);
    const auto cancels = this->sink.ofType(EventType::Cancelled);
    ASSERT_EQ(cancels.size(), 1u);
    EXPECT_EQ(cancels[0].quantity, 3u);
    EXPECT_FALSE(this->book.bestAsk().has_value());
    EXPECT_EQ(this->book.bestBid(), 98);
}

TYPED_TEST(BookTest, FillOrKillWithoutEnoughLiquidityDoesNothing) {
    this->book.addOrder({1, Side::Sell, 100, 2});
    this->book.addOrder({2, Side::Sell, 101, 2});
    this->book.addOrder({3, Side::Sell, 103, 9});
    this->sink.events.clear();
    const Result result = this->book.addOrder({4, Side::Buy, 102, 5, OrderType::FillOrKill});

    EXPECT_EQ(result.filledQuantity, 0u);
    EXPECT_TRUE(this->fills().empty());
    ASSERT_EQ(this->sink.ofType(EventType::Cancelled).size(), 1u);
    EXPECT_EQ(this->sink.ofType(EventType::Cancelled)[0].quantity, 5u);
    EXPECT_FALSE(this->book.bestBid().has_value());
    EXPECT_EQ(this->book.depth(Side::Sell, 10),
              (std::vector<LevelInfo>{{100, 2, 1}, {101, 2, 1}, {103, 9, 1}}));
}

TYPED_TEST(BookTest, FillOrKillWithEnoughLiquidityFillsCompletely) {
    this->book.addOrder({1, Side::Sell, 100, 2});
    this->book.addOrder({2, Side::Sell, 101, 4});
    const Result result = this->book.addOrder({3, Side::Buy, 101, 5, OrderType::FillOrKill});

    EXPECT_EQ(result.filledQuantity, 5u);
    EXPECT_EQ(this->fills().size(), 2u);
    EXPECT_TRUE(this->sink.ofType(EventType::Cancelled).empty());
    EXPECT_EQ(this->book.depth(Side::Sell, 10), (std::vector<LevelInfo>{{101, 1, 1}}));
}

TYPED_TEST(BookTest, PostOnlyThatWouldCrossIsRejected) {
    this->book.addOrder({1, Side::Sell, 100, 2});
    this->sink.events.clear();
    const Result result = this->book.addOrder({2, Side::Buy, 100, 1, OrderType::PostOnly});

    EXPECT_EQ(result.status, Status::Rejected);
    EXPECT_EQ(result.reason, RejectReason::WouldCross);
    ASSERT_EQ(this->sink.events.size(), 1u);
    EXPECT_EQ(this->sink.events[0].type, EventType::Rejected);
    EXPECT_FALSE(this->book.bestBid().has_value());
    EXPECT_EQ(this->book.volumeAtPrice(Side::Sell, 100), 2u);
}

TYPED_TEST(BookTest, PostOnlyThatDoesNotCrossRests) {
    this->book.addOrder({1, Side::Sell, 100, 2});
    const Result result = this->book.addOrder({2, Side::Buy, 99, 1, OrderType::PostOnly});

    EXPECT_EQ(result.status, Status::Accepted);
    EXPECT_EQ(this->book.bestBid(), 99);
}

} // namespace
} // namespace lob::test
