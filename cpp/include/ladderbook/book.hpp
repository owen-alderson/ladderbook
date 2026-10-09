#pragma once
// Order book for one binary contract, kept entirely in YES prices.
//
// Kalshi's book only holds bids: people bidding for YES and people bidding for NO.
// A NO bid at 95c is the same thing as a YES offer at 5c, so with `use_yes_price`
// every NO bid is stored here as a YES ask. One price scale, two sides.
#include <functional>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#include "ladderbook/units.hpp"

namespace ladderbook {

enum class Side { Bid, Ask };

class BinaryBook {
public:
    using Level = std::pair<Price, Qty>;

    void clear() {
        bids_.clear();
        asks_.clear();
    }

    // Replace a level outright (used when loading a snapshot). qty == 0 removes it.
    void set_level(Side side, Price price, Qty qty) {
        check_price(price);
        if (qty < 0) throw std::invalid_argument("negative level size");
        if (side == Side::Bid) set(bids_, price, qty);
        else set(asks_, price, qty);
    }

    // Apply an incremental change. A level can never go negative: if it would, the
    // feed and the book have diverged, and that is reported rather than papered over.
    void apply_delta(Side side, Price price, Qty delta) {
        check_price(price);
        if (side == Side::Bid) add(bids_, price, delta);
        else add(asks_, price, delta);
    }

    std::optional<Price> best_bid() const {
        if (bids_.empty()) return std::nullopt;
        return bids_.begin()->first;
    }
    std::optional<Price> best_ask() const {
        if (asks_.empty()) return std::nullopt;
        return asks_.begin()->first;
    }

    Qty depth_at(Side side, Price price) const {
        return side == Side::Bid ? find(bids_, price) : find(asks_, price);
    }

    // Bids best (highest) first, asks best (lowest) first.
    std::vector<Level> levels(Side side) const {
        std::vector<Level> out;
        if (side == Side::Bid) out.assign(bids_.begin(), bids_.end());
        else out.assign(asks_.begin(), asks_.end());
        return out;
    }

    bool crossed() const {
        auto b = best_bid();
        auto a = best_ask();
        return b && a && *b >= *a;
    }

private:
    using BidMap = std::map<Price, Qty, std::greater<Price>>;
    using AskMap = std::map<Price, Qty>;

    static void check_price(Price price) {
        if (price < 0 || price > kPriceOne) throw std::invalid_argument("price out of [0, 1]");
    }

    template <class M>
    static Qty find(const M& levels, Price price) {
        auto it = levels.find(price);
        return it == levels.end() ? 0 : it->second;
    }

    template <class M>
    static void set(M& levels, Price price, Qty qty) {
        if (qty == 0) levels.erase(price);
        else levels[price] = qty;
    }

    template <class M>
    static void add(M& levels, Price price, Qty delta) {
        auto it = levels.find(price);
        const Qty current = it == levels.end() ? 0 : it->second;
        const Qty next = current + delta;
        if (next < 0) {
            throw std::runtime_error("level at price " + std::to_string(price) + " would go negative (" +
                                     std::to_string(current) + " + " + std::to_string(delta) + ")");
        }
        set(levels, price, next);
    }

    BidMap bids_;
    AskMap asks_;
};

}  // namespace ladderbook
