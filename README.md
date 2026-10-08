# ladderbook

Market making research for Kalshi's hourly crypto strike ladders.

Kalshi lists "Will BTC be above $X at 3pm?" for about 20 strikes every hour, and each contract pays $1 or $0. ladderbook asks whether you can quote both sides of these contracts at prices consistent with the options market and keep the spread without getting picked off when BTC moves. It answers that with a backtester honest enough to trust.

**Status: early development.** Built so far:

- **Recorder.** Streams every open BTC/ETH hourly market's order book, trades and the settlement index (CF Benchmarks BRTI) from Kalshi, plus Deribit's options surface. Messages are stored verbatim in Parquet. Sequence gaps are detected, marked and resynced, never guessed.
- **Verifier.** Rebuilds every book from its deltas and checks it level-for-level against each snapshot Kalshi sends.
- **C++ core.** Exact fixed-point units, a binary order book (a NO bid at 95¢ is a YES offer at 5¢), and Kalshi's fee and rounding model, which matches the documented examples to the micro-dollar.

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
