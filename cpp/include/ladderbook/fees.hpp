#pragma once
// Kalshi fee model, per https://docs.kalshi.com/getting_started/fee_rounding (checked 2026-10-09).
//
//   model fee  = rate * multiplier * C * P * (1 - P)        (dollars)
//   trade fee  = model fee rounded UP to $0.000001
//   balance    = aligned to the member's precision ($0.01 for FCM-cleared retail);
//                the shortfall is a "rounding fee" accumulated per order and rebated
//                back in whole precision steps, never making a fill's net fee negative.
//
// Rates and multipliers are passed in as integers in 1e-4 units (0.07 -> 700, 1.0 -> 10000)
// so the whole computation is exact integer arithmetic.
#include <cstdint>
#include <stdexcept>

#include "ladderbook/units.hpp"

namespace ladderbook {

inline constexpr std::int64_t kTakerRate = 700;  // 0.07
inline constexpr std::int64_t kMakerRate = 175;  // 0.0175, only on series with maker fees

// Trade fee in micro-dollars for `qty` contracts at `price`.
inline Micros trade_fee_micros(std::int64_t rate, std::int64_t multiplier, Price price, Qty qty) {
    if (price < 0 || price > kPriceOne) throw std::invalid_argument("price out of [0, 1]");
    if (qty < 0 || rate < 0 || multiplier < 0) throw std::invalid_argument("negative fee input");
    // rate/1e4 * mult/1e4 * qty/1e2 * price/1e4 * (1e4-price)/1e4 dollars, times 1e6 for micros:
    //   numerator / 1e12. The product overflows int64 for big orders, so use 128-bit.
    using u128 = unsigned __int128;
    const u128 num = static_cast<u128>(rate) * static_cast<u128>(multiplier) * static_cast<u128>(qty) *
                     static_cast<u128>(price) * static_cast<u128>(kPriceOne - price);
    const u128 den = 1'000'000'000'000ULL;
    return static_cast<Micros>((num + den - 1) / den);  // ceil
}

inline Micros floor_to(Micros value, Micros step) {
    Micros q = value / step;
    if (value % step != 0 && value < 0) --q;
    return q * step;
}

struct FillCash {
    Micros balance_change;  // what actually hits the account, on the precision grid
    Micros trade_fee;
    Micros rounding_fee;
    Micros rebate;
};

// Tracks one order's rounding accumulator across its fills.
class OrderFeeAccumulator {
public:
    explicit OrderFeeAccumulator(Micros precision = 10'000) : precision_(precision) {
        if (precision <= 0) throw std::invalid_argument("precision must be positive");
    }

    // revenue: signed cash from the trade itself (negative when buying), in micros.
    FillCash apply(Micros revenue, Micros trade_fee) {
        const Micros raw = revenue - trade_fee;
        const Micros aligned = floor_to(raw, precision_);
        const Micros rounding = raw - aligned;
        accumulated_ += rounding;
        Micros rebate = floor_to(accumulated_, precision_);
        const Micros cap = floor_to(trade_fee + rounding, precision_);  // net fee stays >= 0
        if (rebate > cap) rebate = cap;
        accumulated_ -= rebate;
        return {aligned + rebate, trade_fee, rounding, rebate};
    }

    Micros carried() const { return accumulated_; }

private:
    Micros precision_;
    Micros accumulated_ = 0;
};

}  // namespace ladderbook
