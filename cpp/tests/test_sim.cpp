#include <catch2/catch_test_macros.hpp>

#include "ladderbook/sim.hpp"

using namespace ladderbook;

namespace {

constexpr Qty C = kQtyOne;  // one contract
constexpr Nanos MS = 1'000'000;

SimConfig instant(QueueModel model = QueueModel::Proportional) {
    SimConfig cfg;
    cfg.order_latency = 0;
    cfg.cancel_latency = 0;
    cfg.queue_model = model;
    return cfg;
}

// A book with 500 contracts bid at 40c and 300 offered at 45c.
SimExchange seeded(SimConfig cfg) {
    SimExchange ex(cfg);
    ex.on_clear(0);
    ex.on_level(0, Side::Bid, 4000, 500 * C);
    ex.on_level(0, Side::Ask, 4500, 300 * C);
    return ex;
}

}  // namespace

TEST_CASE("a post-only order that would cross is rejected") {
    auto ex = seeded(instant());
    auto id = ex.place(1, Side::Bid, 4500, 10 * C);
    REQUIRE(ex.order(id).status == OrderStatus::Rejected);
    REQUIRE(ex.take_fills().empty());
}

TEST_CASE("we fill only after the size ahead of us has traded") {
    auto ex = seeded(instant());
    auto id = ex.place(1, Side::Bid, 4000, 100 * C);
    REQUIRE(ex.order(id).queue_ahead == 500 * C);

    ex.on_trade(2, 4000, 300 * C, Side::Ask);
    REQUIRE(ex.take_fills().empty());
    REQUIRE(ex.order(id).queue_ahead == 200 * C);

    ex.on_trade(3, 4000, 250 * C, Side::Ask);  // 200 ahead, then 50 of ours
    auto fills = ex.take_fills();
    REQUIRE(fills.size() == 1);
    REQUIRE(fills[0].qty == 50 * C);
    REQUIRE(fills[0].maker);
    REQUIRE(ex.position() == 50 * C);
}

TEST_CASE("the book decrement that follows a trade is not also counted as a cancel") {
    auto ex = seeded(instant(QueueModel::Optimistic));
    auto id = ex.place(1, Side::Bid, 4000, 100 * C);
    ex.on_trade(2, 4000, 300 * C, Side::Ask);
    ex.on_delta(2, Side::Bid, 4000, -300 * C);
    REQUIRE(ex.order(id).queue_ahead == 200 * C);  // optimistic would have dropped it to 0 otherwise
}

TEST_CASE("cancels move us up according to the queue model") {
    auto run = [](QueueModel model) {
        auto ex = seeded(instant(model));
        auto id = ex.place(1, Side::Bid, 4000, 10 * C);       // 500 ahead of us
        ex.on_delta(2, Side::Bid, 4000, 500 * C);             // 500 join behind us
        ex.on_delta(3, Side::Bid, 4000, -400 * C);            // 400 cancel, no trade
        return ex.order(id).queue_ahead;
    };
    REQUIRE(run(QueueModel::Conservative) == 500 * C);
    REQUIRE(run(QueueModel::Proportional) == 300 * C);  // 400 * 500/1000 were ahead
    REQUIRE(run(QueueModel::Optimistic) == 100 * C);
}

TEST_CASE("nobody can stay ahead of us beyond the size left at the level") {
    auto ex = seeded(instant(QueueModel::Conservative));
    auto id = ex.place(1, Side::Bid, 4000, 10 * C);
    ex.on_delta(2, Side::Bid, 4000, -450 * C);
    REQUIRE(ex.order(id).queue_ahead == 50 * C);
}

TEST_CASE("a trade through our price fills us first, sharing the taker's size") {
    auto ex = seeded(instant());
    auto better = ex.place(1, Side::Bid, 4200, 30 * C);
    auto worse = ex.place(1, Side::Bid, 4100, 30 * C);
    ex.on_trade(2, 4000, 40 * C, Side::Ask);  // seller hit 40c: would have hit our 42c then 41c
    auto fills = ex.take_fills();
    REQUIRE(fills.size() == 2);
    REQUIRE(fills[0].order_id == better);
    REQUIRE(fills[0].qty == 30 * C);
    REQUIRE(fills[1].order_id == worse);
    REQUIRE(fills[1].qty == 10 * C);
}

TEST_CASE("trades on the other side never fill us") {
    auto ex = seeded(instant());
    ex.place(1, Side::Bid, 4000, 10 * C);
    ex.on_trade(2, 4500, 300 * C, Side::Bid);  // a buyer lifted the offer
    REQUIRE(ex.take_fills().empty());
}

TEST_CASE("orders only exist once they reach the exchange") {
    SimConfig cfg = instant();
    cfg.order_latency = 50 * MS;
    auto ex = seeded(cfg);
    auto id = ex.place(0, Side::Bid, 4100, 10 * C);
    ex.on_trade(10 * MS, 4000, 100 * C, Side::Ask);  // would trade through us, but we're not there yet
    REQUIRE(ex.take_fills().empty());
    ex.advance(50 * MS);
    REQUIRE(ex.order(id).status == OrderStatus::Resting);
}

TEST_CASE("a fill can still happen while a cancel is in flight") {
    SimConfig cfg = instant();
    cfg.cancel_latency = 50 * MS;
    auto ex = seeded(cfg);
    auto id = ex.place(0, Side::Bid, 4100, 10 * C);
    ex.cancel(1 * MS, id);
    ex.on_trade(20 * MS, 4000, 100 * C, Side::Ask);
    REQUIRE(ex.take_fills().size() == 1);
    ex.advance(51 * MS);
    REQUIRE(ex.order(id).status == OrderStatus::Filled);
}

TEST_CASE("queue position is measured when the order arrives, not when it is sent") {
    SimConfig cfg = instant();
    cfg.order_latency = 50 * MS;
    auto ex = seeded(cfg);
    auto id = ex.place(0, Side::Bid, 4000, 10 * C);
    ex.on_delta(10 * MS, Side::Bid, 4000, 200 * C);  // 200 more join before we arrive
    ex.advance(50 * MS);
    REQUIRE(ex.order(id).queue_ahead == 700 * C);
}

TEST_CASE("cash, fees and settlement add up exactly") {
    SimConfig cfg = instant();
    cfg.maker_rate = kMakerRate;  // a series with maker fees
    auto ex = seeded(cfg);
    ex.place(1, Side::Bid, 4100, 1 * C);
    ex.on_trade(2, 4000, 1 * C, Side::Ask);
    auto fills = ex.take_fills();
    REQUIRE(fills.size() == 1);
    // 1 contract at 41c: maker fee 0.0175 * 0.41 * 0.59 = $0.00423325 -> $0.004234 trade fee;
    // the balance moves on the cent grid: -0.414234 -> -$0.42
    REQUIRE(fills[0].balance_change == -420'000);
    REQUIRE(ex.cash() == -420'000);
    ex.settle(3, true);
    REQUIRE(ex.cash() == 580'000);
    REQUIRE(ex.position() == 0);
}

TEST_CASE("selling YES you don't own is a short that pays out if YES wins") {
    auto ex = seeded(instant());
    ex.place(1, Side::Ask, 4400, 1 * C);
    ex.on_trade(2, 4400, 1 * C, Side::Bid);  // a buyer lifted 44c... our offer is the only one there
    REQUIRE(ex.position() == -1 * C);
    REQUIRE(ex.cash() == 440'000);
    ex.settle(3, true);
    REQUIRE(ex.cash() == 440'000 - 1'000'000);
}

TEST_CASE("immediate-or-cancel walks the book at taker fees and cancels the rest") {
    auto ex = seeded(instant());
    ex.on_level(0, Side::Ask, 4600, 100 * C);
    auto id = ex.place(1, Side::Bid, 4600, 500 * C, OrderType::ImmediateOrCancel);
    auto fills = ex.take_fills();
    REQUIRE(fills.size() == 2);
    REQUIRE(fills[0].price == 4500);
    REQUIRE(fills[0].qty == 300 * C);
    REQUIRE(fills[1].price == 4600);
    REQUIRE(fills[1].qty == 100 * C);
    REQUIRE_FALSE(fills[0].maker);
    REQUIRE(fills[0].fee > 0);
    REQUIRE(ex.order(id).status == OrderStatus::Cancelled);
    REQUIRE(ex.book().depth_at(Side::Ask, 4500) == 300 * C);  // the recorded book is never changed by us
}

TEST_CASE("a snapshot caps the queue ahead and empties missing levels") {
    auto ex = seeded(instant(QueueModel::Conservative));
    auto at40 = ex.place(1, Side::Bid, 4000, 10 * C);
    ex.on_level(1, Side::Bid, 3900, 100 * C);
    auto at39 = ex.place(1, Side::Bid, 3900, 10 * C);
    ex.on_clear(2);
    ex.on_level(2, Side::Bid, 4000, 120 * C);  // 39c is gone from the snapshot
    REQUIRE(ex.order(at40).queue_ahead == 120 * C);
    REQUIRE(ex.order(at39).queue_ahead == 0);
}

TEST_CASE("time cannot go backwards") {
    auto ex = seeded(instant());
    ex.advance(10);
    REQUIRE_THROWS(ex.on_trade(5, 4000, C, Side::Ask));
}
