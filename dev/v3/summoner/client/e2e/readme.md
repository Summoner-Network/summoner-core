# client -- e2e  
## Official Testing Suite for Summoner Implementations

This is the canonical **end-to-end (E2E) testing suite** for validating the **performance** and **correctness** of any Summoner-compatible platform, runtime, or transaction executor.

The test suite is designed to evaluate both the functional behavior and underlying concurrency model of a Summoner node. It does this by simulating real-world usage patterns — including bursts of transactions, interleaved reads/writes, and conflict-heavy workloads — and verifying consistent results across multiple executions.

### Purpose
- **Correctness:** Ensure deterministic output across multiple executions of the same transaction block, even under parallelism and speculative execution.
- **Performance:** Stress-test throughput and latency characteristics of different Summoner backends (e.g., native, WASM, STM-enabled).
- **Compatibility:** Serve as the baseline for protocol adherence, used by all client implementations to qualify for production-readiness.

---

### Test Coverage

#### ✔ Basic Functional Tests
- Counter service increment/decrement behavior
- Read/write consistency
- Transactional rollback on failure

#### ✔ Concurrency Tests
- Concurrent increments under load
- Read skew and write conflict detection
- Deterministic replay with dependency tracking

#### ✔ Stress & Load Tests
- Randomized workloads with controlled entropy
- Conflict rate tuning
- Thread scaling and parallelism profiling

#### ✔ Snapshot & Replay Validation
- Re-run the same test block multiple times
- Verify output hashes match across executions
- Confirm re-execution paths are deterministic

---

### Usage
Run against any Summoner implementation by pointing to the gRPC endpoint of the deployed counting service:

```bash
cargo run --bin summoner-e2e -- --endpoint http://localhost:50051
```

### Extending the Suite
- Add new domain-specific tests by implementing the `Scenario` trait.
- Plug in custom workloads and verify they produce consistent world states.
- Leverage the included profiler hooks to gather metrics across test runs.