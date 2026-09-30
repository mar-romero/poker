# Performance Budgets
Budgets are workload-specific and should be calibrated on a documented reference machine; numbers below are initial engineering targets, not user promises.

- State replay incremental update: p95 <= 5 ms per normalized action on ordinary local hardware.
- Cached HUD stat refresh for one player/stat pack: p95 <= 50 ms; uncached filtered popup query p95 <= 250 ms on reference dataset.
- DecisionSpot assembly excluding solver: p95 <= 50 ms once source data is available.
- Exact equity: use a configurable state-count/time budget; switch to Monte Carlo rather than freezing UI.
- Monte Carlo: progressive result/cancellation; first useful estimate target <= 100 ms, improved precision continues asynchronously within request lifetime.
- HUD UI thread: no blocking database/simulation/solver calls.
- Replayer step: p95 <= 50 ms with cached analysis; otherwise show progressive loading state.
- Solver/re-solving: explicit per-job latency/iteration/memory budgets; unconverged jobs return uncertainty status.

Benchmarks must report CPU, core count, RAM, OS, Python/native versions, dataset size, cache state, and configuration.
