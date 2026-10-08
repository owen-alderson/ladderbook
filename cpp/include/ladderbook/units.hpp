#pragma once
// Fixed-point units used everywhere. No floating point touches a price, size or balance.
//
//   Price: 1e-4 dollars (0.01 cents). Kalshi sends "0.0800" -> 800. Range [0, 10000].
//   Qty:   1e-2 contracts. Kalshi sends "300.00" -> 30000.
//   Micros: 1e-6 dollars. Fees are computed at this precision.
#include <cstdint>

namespace ladderbook {

using Price = std::int32_t;
using Qty = std::int64_t;
using Micros = std::int64_t;

inline constexpr Price kPriceOne = 10000;  // $1.00, the payout of a winning contract
inline constexpr Qty kQtyOne = 100;        // one whole contract

// Cost of `qty` contracts at `price`, in micro-dollars (exact: 1e-4 * 1e-2 = 1e-6).
inline constexpr Micros notional_micros(Price price, Qty qty) {
    return static_cast<Micros>(price) * qty;
}

}  // namespace ladderbook
