#include <catch2/catch_test_macros.hpp>

#include "ladderbook/fees.hpp"

using namespace ladderbook;

TEST_CASE("trade fee matches Kalshi's documented example") {
    // 1 contract at 5.5c: model fee $0.00363825 -> trade fee $0.003639
    REQUIRE(trade_fee_micros(kTakerRate, 10000, 550, 100) == 3639);
}

TEST_CASE("trade fee is symmetric in P and 1-P and zero at the bounds") {
    REQUIRE(trade_fee_micros(kTakerRate, 10000, 3000, 1000) == trade_fee_micros(kTakerRate, 10000, 7000, 1000));
    REQUIRE(trade_fee_micros(kTakerRate, 10000, 0, 1000) == 0);
    REQUIRE(trade_fee_micros(kTakerRate, 10000, 10000, 1000) == 0);
}

TEST_CASE("trade fee at 50c is 0.07 * 0.25 per contract") {
    // 100 contracts at 50c: 0.07 * 100 * 0.25 = $1.75 exactly
    REQUIRE(trade_fee_micros(kTakerRate, 10000, 5000, 100 * kQtyOne) == 1'750'000);
}

TEST_CASE("fee multiplier scales and zero multiplier is free") {
    REQUIRE(trade_fee_micros(kTakerRate, 5000, 5000, 100 * kQtyOne) == 875'000);
    REQUIRE(trade_fee_micros(kTakerRate, 0, 5000, 100 * kQtyOne) == 0);
}

TEST_CASE("large orders do not overflow") {
    // 1,000,000 contracts at 50c: $17,500
    REQUIRE(trade_fee_micros(kTakerRate, 10000, 5000, 1'000'000 * kQtyOne) == 17'500'000'000);
}

TEST_CASE("invalid inputs are rejected, not clamped") {
    REQUIRE_THROWS(trade_fee_micros(kTakerRate, 10000, -1, 100));
    REQUIRE_THROWS(trade_fee_micros(kTakerRate, 10000, 10001, 100));
    REQUIRE_THROWS(trade_fee_micros(kTakerRate, 10000, 5000, -100));
}

TEST_CASE("rounding fee matches Kalshi's FCM-cleared example") {
    OrderFeeAccumulator acc(10'000);  // $0.01 precision
    auto cash = acc.apply(-55'000, 3'639);
    REQUIRE(cash.balance_change == -60'000);
    REQUIRE(cash.rounding_fee == 1'361);
    REQUIRE(cash.trade_fee + cash.rounding_fee == 5'000);
    REQUIRE(cash.rebate == 0);
}

TEST_CASE("accumulated rounding is rebated in whole precision steps") {
    // Mirrors the docs table: each fill leaves $0.004 of overpayment.
    OrderFeeAccumulator acc(10'000);
    // revenue -1.000000, trade fee 0.006000 -> raw -1.006, aligned -1.01, rounding 0.004
    REQUIRE(acc.apply(-1'000'000, 6'000).rebate == 0);
    REQUIRE(acc.carried() == 4'000);
    REQUIRE(acc.apply(-1'000'000, 6'000).rebate == 0);
    REQUIRE(acc.carried() == 8'000);
    auto third = acc.apply(-1'000'000, 6'000);
    REQUIRE(third.rebate == 10'000);
    REQUIRE(acc.carried() == 2'000);
    REQUIRE(third.balance_change == -1'000'000);
}

TEST_CASE("rebate never makes a fill's net fee negative") {
    OrderFeeAccumulator acc(10'000);
    acc.apply(-1'000'000, 9'000);  // carries 0.001
    for (int i = 0; i < 20; ++i) {
        auto c = acc.apply(-1'000'000, 9'000);
        REQUIRE(c.trade_fee + c.rounding_fee - c.rebate >= 0);
    }
}
