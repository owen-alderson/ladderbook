#pragma once
// Simulated exchange for one Kalshi market, driven by a recorded tape.
//
// The recorded book never contains our orders, so the question is always "if our order
// had been resting there, when would it have filled?". The rules:
//
//  * Latency. An order or cancel sent at time t reaches the exchange at t + latency.
//    Everything on the tape before then happens without it.
//  * Queue position. A new order joins the back of its price level: everything shown
//    there on arrival is ahead of it.
//  * Trades eat the queue from the front. A trade at our price first uses up the size
//    ahead of us, then fills us. A trade through our price (a seller hitting 39c while
//    we bid 40c) means the taker would have hit us first, so it fills us up to its size.
//  * Size that leaves a level without a trade is a cancel. Whether it was ahead of us
//    is unknowable; QueueModel picks the assumption, so results can be bracketed:
//      Conservative: cancels are always behind us (our place never improves)
//      Proportional: in proportion to the size ahead of and behind us
//      Optimistic:   cancels are always ahead of us
//  * A trade also shows up as a book decrement. The trade's size is credited to its
//    level, and decrements spend that credit before counting as cancels, so one fill is
//    never counted twice.
//  * Replay is exogenous: our orders never change the recorded book (no market impact).
//    That is the standard backtest assumption and the reason sizes stay small.
//  * Prices are YES prices. Selling YES you don't own is buying NO; position is one
//    signed number, cash and fees are exact integers.
#include <algorithm>
#include <cstdint>
#include <deque>
#include <map>
#include <stdexcept>
#include <vector>

#include "ladderbook/book.hpp"
#include "ladderbook/fees.hpp"
#include "ladderbook/units.hpp"

namespace ladderbook {

using Nanos = std::int64_t;
using OrderId = std::uint64_t;

enum class QueueModel { Conservative, Proportional, Optimistic };
enum class OrderType { PostOnly, ImmediateOrCancel };

struct SimConfig {
    Nanos order_latency = 50'000'000;   // 50 ms
    Nanos cancel_latency = 50'000'000;
    QueueModel queue_model = QueueModel::Proportional;
    std::int64_t maker_rate = 0;        // 1e-4 units; 0 on series without maker fees (e.g. KXBTCD)
    std::int64_t taker_rate = kTakerRate;
    std::int64_t fee_multiplier = 10'000;
    Micros balance_precision = 10'000;  // $0.01 for FCM-cleared retail accounts
};

struct Fill {
    Nanos ts;
    OrderId order_id;
    Side side;  // Bid = we bought YES
    Price price;
    Qty qty;
    bool maker;
    Micros fee;             // trade fee + rounding fee - rebate
    Micros balance_change;  // cash effect including fees
};

enum class OrderStatus { Pending, Resting, Filled, Cancelled, Rejected };

struct OrderState {
    OrderId id;
    Side side;
    Price price;
    Qty qty;        // original size
    Qty remaining;
    OrderType type;
    OrderStatus status;
    Qty queue_ahead = 0;
};

class SimExchange {
public:
    explicit SimExchange(SimConfig config = {}) : cfg_(config) {}

    // ---- market data (in tape order) -------------------------------------------------

    // A snapshot starts: the book is rebuilt from the on_level calls that follow.
    // Nobody can be ahead of us beyond what the snapshot shows at our level, and a level
    // missing from it is empty.
    void on_clear(Nanos ts) {
        advance(ts);
        book_.clear();
        credit_.clear();
        before_snapshot_.clear();
        for (auto& [id, o] : orders_) {
            if (o.status != OrderStatus::Resting) continue;
            before_snapshot_[id] = o.queue_ahead;
            o.queue_ahead = 0;
        }
    }

    void on_level(Nanos ts, Side side, Price price, Qty qty) {
        advance(ts);
        book_.set_level(side, price, qty);
        for (auto& [id, o] : orders_) {
            if (o.status == OrderStatus::Resting && o.side == side && o.price == price) {
                auto it = before_snapshot_.find(id);
                o.queue_ahead = it == before_snapshot_.end() ? qty : std::min(it->second, qty);
            }
        }
    }

    void on_delta(Nanos ts, Side side, Price price, Qty delta) {
        advance(ts);
        const Qty before = book_.depth_at(side, price);
        book_.apply_delta(side, price, delta);
        if (delta >= 0) return;  // new size joins the back of the queue
        Qty removed = -delta;
        auto key = std::make_pair(side, price);
        auto it = credit_.find(key);
        if (it != credit_.end()) {
            const Qty traded = std::min(removed, it->second);
            removed -= traded;
            it->second -= traded;
            if (it->second == 0) credit_.erase(it);
        }
        if (removed > 0) apply_cancel(side, price, removed, before);
    }

    // A trade at `price`. aggressor == Bid: a buyer lifted YES offers (fills our asks);
    // aggressor == Ask: a seller hit YES bids (fills our bids).
    void on_trade(Nanos ts, Price price, Qty qty, Side aggressor) {
        advance(ts);
        const Side resting = aggressor == Side::Bid ? Side::Ask : Side::Bid;
        credit_[{resting, price}] += qty;
        // Our orders in price priority (best first), then time priority.
        std::vector<OrderState*> ours;
        for (auto& [id, o] : orders_)
            if (o.status == OrderStatus::Resting && o.side == resting) ours.push_back(&o);
        std::stable_sort(ours.begin(), ours.end(), [resting](const OrderState* a, const OrderState* b) {
            return resting == Side::Bid ? a->price > b->price : a->price < b->price;
        });
        Qty budget = qty;  // the taker's size: shared, never handed out twice
        for (OrderState* o : ours) {
            if (budget == 0) break;
            const bool through = resting == Side::Bid ? o->price > price : o->price < price;
            if (through) {
                const Qty take = std::min(o->remaining, budget);
                budget -= take;
                fill(ts, *o, take, true);
            } else if (o->price == price) {
                const Qty used = std::min(budget, o->queue_ahead);
                o->queue_ahead -= used;
                budget -= used;
                const Qty take = std::min(budget, o->remaining);
                budget -= take;
                fill(ts, *o, take, true);
            }
        }
    }

    // ---- strategy actions ------------------------------------------------------------

    OrderId place(Nanos ts, Side side, Price price, Qty qty, OrderType type = OrderType::PostOnly) {
        if (price <= 0 || price >= kPriceOne) throw std::invalid_argument("order price must be inside (0, 1)");
        if (qty <= 0) throw std::invalid_argument("order size must be positive");
        advance(ts);
        const OrderId id = next_id_++;
        orders_.emplace(id, OrderState{id, side, price, qty, qty, type, OrderStatus::Pending});
        pending_.push_back({ts + cfg_.order_latency, id, false});
        advance(ts);  // with zero latency it arrives now
        return id;
    }

    void cancel(Nanos ts, OrderId id) {
        advance(ts);
        if (!orders_.count(id)) throw std::invalid_argument("unknown order id");
        pending_.push_back({ts + cfg_.cancel_latency, id, true});
        advance(ts);
    }

    // Apply every action that has reached the exchange by time ts.
    void advance(Nanos ts) {
        if (ts < now_) throw std::invalid_argument("time went backwards");
        now_ = ts;
        std::stable_sort(pending_.begin(), pending_.end(),
                         [](const Action& a, const Action& b) { return a.arrival < b.arrival; });
        while (!pending_.empty() && pending_.front().arrival <= ts) {
            const Action a = pending_.front();
            pending_.pop_front();
            OrderState& o = orders_.at(a.id);
            if (a.is_cancel) {
                if (o.status == OrderStatus::Resting || o.status == OrderStatus::Pending)
                    o.status = OrderStatus::Cancelled;
            } else if (o.status == OrderStatus::Pending) {
                arrive(a.arrival, o);
            }
        }
    }

    // ---- settlement and state --------------------------------------------------------

    // Pays $1 per YES contract held if the market resolved YES; a short YES position pays.
    void settle(Nanos ts, bool yes) {
        advance(ts);
        for (auto& [id, o] : orders_) {
            if (o.status == OrderStatus::Resting || o.status == OrderStatus::Pending) o.status = OrderStatus::Cancelled;
        }
        if (yes) cash_ += notional_micros(kPriceOne, position_);
        position_ = 0;
        settled_ = true;
    }

    std::vector<Fill> take_fills() {
        std::vector<Fill> out;
        out.swap(fills_);
        return out;
    }

    const BinaryBook& book() const { return book_; }
    const OrderState& order(OrderId id) const { return orders_.at(id); }
    Qty position() const { return position_; }
    Micros cash() const { return cash_; }
    Micros fees_paid() const { return fees_; }
    bool settled() const { return settled_; }

    std::vector<OrderState> open_orders() const {
        std::vector<OrderState> out;
        for (const auto& [id, o] : orders_)
            if (o.status == OrderStatus::Resting || o.status == OrderStatus::Pending) out.push_back(o);
        return out;
    }

private:
    struct Action {
        Nanos arrival;
        OrderId id;
        bool is_cancel;
    };

    void arrive(Nanos ts, OrderState& o) {
        const auto best_opposite = o.side == Side::Bid ? book_.best_ask() : book_.best_bid();
        const bool crosses = best_opposite && (o.side == Side::Bid ? o.price >= *best_opposite : o.price <= *best_opposite);
        if (o.type == OrderType::PostOnly) {
            if (crosses) {
                o.status = OrderStatus::Rejected;  // Kalshi rejects post-only orders that would take
                return;
            }
            o.status = OrderStatus::Resting;
            o.queue_ahead = book_.depth_at(o.side, o.price);
            return;
        }
        // Immediate-or-cancel: walk the opposite side up to our limit, then cancel the rest.
        const Side opposite = o.side == Side::Bid ? Side::Ask : Side::Bid;
        for (const auto& [price, size] : book_.levels(opposite)) {
            const bool ok = o.side == Side::Bid ? price <= o.price : price >= o.price;
            if (!ok || o.remaining == 0) break;
            fill(ts, o, std::min(size, o.remaining), false, price);
        }
        if (o.remaining > 0) o.status = OrderStatus::Cancelled;
    }

    void apply_cancel(Side side, Price price, Qty removed, Qty depth_before) {
        for (auto& [id, o] : orders_) {
            if (o.status != OrderStatus::Resting || o.side != side || o.price != price) continue;
            Qty drop = 0;
            switch (cfg_.queue_model) {
                case QueueModel::Conservative:
                    drop = 0;
                    break;
                case QueueModel::Optimistic:
                    drop = removed;
                    break;
                case QueueModel::Proportional:
                    // share of the cancelled size that was ahead of us, rounded down (conservatively)
                    drop = depth_before > 0 ? static_cast<Qty>(static_cast<__int128>(removed) * o.queue_ahead / depth_before) : 0;
                    break;
            }
            o.queue_ahead = std::max<Qty>(0, o.queue_ahead - drop);
            o.queue_ahead = std::min(o.queue_ahead, book_.depth_at(side, price));
        }
    }

    void fill(Nanos ts, OrderState& o, Qty qty, bool maker, Price at = -1) {
        if (qty <= 0) return;
        const Price price = at < 0 ? o.price : at;
        const std::int64_t rate = maker ? cfg_.maker_rate : cfg_.taker_rate;
        const Micros trade_fee = trade_fee_micros(rate, cfg_.fee_multiplier, price, qty);
        const Micros notional = notional_micros(price, qty);
        const Micros revenue = o.side == Side::Bid ? -notional : notional;
        auto& acc = accumulators_.try_emplace(o.id, cfg_.balance_precision).first->second;
        const FillCash cash = acc.apply(revenue, trade_fee);
        const Micros fee = cash.trade_fee + cash.rounding_fee - cash.rebate;
        o.remaining -= qty;
        if (o.remaining == 0) o.status = OrderStatus::Filled;
        position_ += o.side == Side::Bid ? qty : -qty;
        cash_ += cash.balance_change;
        fees_ += fee;
        fills_.push_back({ts, o.id, o.side, price, qty, maker, fee, cash.balance_change});
    }

    SimConfig cfg_;
    BinaryBook book_;
    std::map<OrderId, OrderState> orders_;
    std::map<OrderId, OrderFeeAccumulator> accumulators_;
    std::map<std::pair<Side, Price>, Qty> credit_;
    std::map<OrderId, Qty> before_snapshot_;
    std::deque<Action> pending_;
    std::vector<Fill> fills_;
    OrderId next_id_ = 1;
    Nanos now_ = 0;
    Qty position_ = 0;
    Micros cash_ = 0;
    Micros fees_ = 0;
    bool settled_ = false;
};

}  // namespace ladderbook
