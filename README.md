# ladderbook

Market making research for Kalshi's hourly crypto strike ladders.

Kalshi lists "Will BTC be above $X at 3pm?" for about 20 strikes every hour, and each contract pays $1 or $0. ladderbook asks whether you can quote both sides of these contracts at prices consistent with the options market and keep the spread without getting picked off when BTC moves. It answers that with a backtester honest enough to trust.

**Status: early development.** Built so far:

- **Recorder.** Streams every open BTC/ETH hourly market's order book, trades and the settlement index (CF Benchmarks BRTI) from Kalshi, plus Deribit's options surface. Messages are stored verbatim in Parquet. Sequence gaps are detected, marked and resynced, never guessed.
- **Verifier.** Rebuilds every book from its deltas and checks it level-for-level against each snapshot Kalshi sends.
- **Fair value.** P(BTC settles above K) from Deribit's options:
  - an SVI smile fitted to the nearest expiry's marks;
  - Kalshi's 60-second-average settlement modelled exactly, including the prints already locked in during the final minute;
  - checked against closed-form Black-Scholes and Monte Carlo.
- **Coherence.** Pairwise Frank-Wolfe finds the closest set of prices that can't contradict each other across an hour's strikes.
- **C++ core.**
  - Exact fixed-point units.
  - A binary order book (a NO bid at 95¢ is a YES offer at 5¢).
  - Kalshi's fee and rounding model, which matches the documented examples to the micro-dollar.
- **Simulated exchange (C++).** Replays the recorded tape against hypothetical orders:
  - order and cancel latency;
  - queue position: you fill only after the size ahead of you trades;
  - trade-throughs;
  - trades netted against their own book decrements, so one fill is never also counted as a cancel;
  - three cancel assumptions (conservative, proportional, optimistic), so every result can be reported as a range rather than a single flattering number.

## Today's ladder (no API key needed)

```bash
ladderbook fair
```

```
KXBTCD-26OCT0820   closes in 34.6 min

      strike    bid    ask     mid  options fair   (cents)
   81,699.99   86.0   87.0   86.50         79.94
   81,799.99   62.0   63.0   62.50         61.33
   81,899.99   33.0   34.0   33.50         37.07
   81,999.99   12.0   13.0   12.50         17.69

                        centre  volatility
options imply        81,836.78      29.6%   Deribit index; nearest-expiry ATM vol
Kalshi implies       81,849.04      21.3%   fitted to the mids above
```

In this snapshot Kalshi priced the next 35 minutes at a much lower volatility than Deribit's options. Whether that is a risk premium, a time-of-day effect or an edge is the question this project answers with data, not by eye.

## Recording

```bash
export KALSHI_KEY_ID=...                     # kalshi.com → Profile → API Keys
export KALSHI_PRIVATE_KEY_PATH=~/kalshi.pem
ladderbook record --out data                 # Ctrl-C to stop; files land in data/raw/YYYY-MM-DD/
ladderbook verify --data data                # prove the books rebuilt exactly
```

## Development

```bash
python -m venv .venv && .venv/bin/pip install scikit-build-core pybind11 pytest
.venv/bin/pip install -e . --no-build-isolation
.venv/bin/pytest

# C++ tests
cmake -S . -B build/tests -DLADDERBOOK_BUILD_TESTS=ON && cmake --build build/tests && ./build/tests/core_tests
```

MIT licensed.
