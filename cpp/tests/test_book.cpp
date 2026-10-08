#include <catch2/catch_test_macros.hpp>

#include "ladderbook/book.hpp"

using namespace ladderbook;

TEST_CASE("empty book has no best prices and is not crossed") {
    BinaryBook book;
    REQUIRE_FALSE(book.best_bid());
    REQUIRE_FALSE(book.best_ask());
    REQUIRE_FALSE(book.crossed());
}

TEST_CASE("best bid is the highest, best ask the lowest") {
    BinaryBook book;
    book.set_level(Side::Bid, 300, 1000);
    book.set_level(Side::Bid, 400, 500);
    book.set_level(Side::Ask, 600, 700);
    book.set_level(Side::Ask, 500, 100);
    REQUIRE(*book.best_bid() == 400);
    REQUIRE(*book.best_ask() == 500);
    auto bids = book.levels(Side::Bid);
    REQUIRE(bids.front() == BinaryBook::Level{400, 500});
    auto asks = book.levels(Side::Ask);
    REQUIRE(asks.front() == BinaryBook::Level{500, 100});
}

TEST_CASE("deltas add, remove at zero, and never go negative") {
    BinaryBook book;
    book.apply_delta(Side::Bid, 400, 300);
    book.apply_delta(Side::Bid, 400, 200);
    REQUIRE(book.depth_at(Side::Bid, 400) == 500);
    book.apply_delta(Side::Bid, 400, -500);
    REQUIRE(book.depth_at(Side::Bid, 400) == 0);
    REQUIRE(book.levels(Side::Bid).empty());
    REQUIRE_THROWS_AS(book.apply_delta(Side::Bid, 400, -1), std::runtime_error);
}

TEST_CASE("setting a level to zero removes it") {
    BinaryBook book;
    book.set_level(Side::Ask, 900, 100);
    book.set_level(Side::Ask, 900, 0);
    REQUIRE_FALSE(book.best_ask());
}

TEST_CASE("crossed book is detected") {
    BinaryBook book;
    book.set_level(Side::Bid, 500, 100);
    book.set_level(Side::Ask, 500, 100);
    REQUIRE(book.crossed());
}

TEST_CASE("prices outside [0, 1] are rejected") {
    BinaryBook book;
    REQUIRE_THROWS(book.set_level(Side::Bid, 10001, 1));
    REQUIRE_THROWS(book.apply_delta(Side::Ask, -1, 1));
    REQUIRE_THROWS(book.set_level(Side::Bid, 100, -5));
}
