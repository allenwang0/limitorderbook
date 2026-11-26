# limitorderbook

A limit order book matching engine in C++17: the core of an exchange, pairing buy and sell orders by price and arrival time. Its cache-friendly book processes **58.95M messages/s**, 1.89× the `std::map` version on the same seeded flow.

![Latency percentiles per operation for both books](docs/img/latency.png)

## Highlights

- **Checked op by op** against a simple reference book across 20 seeds of 100,000 ops each, also under AddressSanitizer and UndefinedBehaviorSanitizer.
- **Market, IOC, fill-or-kill and post-only orders**, with O(1) average cancel.
- **No allocation for resting orders** in `FlatBook` after warm-up.

<!-- results:start -->
| Book | Messages/s |
| :--- | ---: |
| MapBook | 31.15M |
| FlatBook | 58.95M |

One thread on Apple M5 Pro, single run, seeded synthetic flow (55% add, 40% cancel, 5% market). Latency percentiles and a replay are in the [report](https://allenwang0.github.io/limitorderbook/).
<!-- results:end -->

## Run it

```sh
git clone https://github.com/allenwang0/limitorderbook.git
cd limitorderbook
cmake --workflow --preset default    # Release build + tests
./build/lob_bench                    # latency and throughput benchmark
./build/lob_replay examples/demo_flow.txt
```

Needs CMake 3.25+, Ninja and a C++17 compiler.

## How it works

- Prices are integer ticks. Orders match best price first, then oldest first, and every fill trades at the resting order's price.
- `MapBook` keeps price levels in a `std::map` and each level's orders in a linked list.
- `FlatBook` keeps orders in a pooled array linked by index, and price levels near the market in a vector indexed by tick, so most operations skip the tree.
- Reducing an order's size keeps its place in the queue; raising it or changing price sends it to the back, as on CME and Nasdaq.
- `lob_replay` turns a file of order commands into JSON fills and book depth.

## Limitations

- One instrument, one thread, no networking or market data feed.
- The benchmark flow is synthetic, not replayed market data.
- The clock ticks every 41 ns, so per-message latencies are coarse; throughput is the better measure.
