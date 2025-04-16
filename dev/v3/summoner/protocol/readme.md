# WASTM gRPC Interface

This gRPC interface defines the core protocol for interacting with the **WASTM Executor**, enabling secure and deterministic deployment and invocation of WebAssembly-based smart contracts.

WASTM (WebAssembly Software Transactional Memory) is a BlockSTM-inspired execution model for WASM runtimes, optimized for speculative parallelism, replayable determinism, and high-throughput smart contract execution.

## Services

### `WASTMExecutor`

The `WASTMExecutor` service exposes two primary methods:

---

#### `Deploy(DeployRequest) → DeployResponse`

Deploys a signed WebAssembly smart contract into the system. Contracts are uniquely identified by a 32-byte contract ID (typically a hash of the WASM bytecode and deployer).

**Request:**
- `signer (bytes)`: Public key or address of the deployer.
- `signature (bytes)`: Signature over the deploy payload.
- `wasm_code (bytes)`: Raw WebAssembly bytecode.
- `contract_name (string)`: Human-readable contract identifier (optional, for logging/UI).

**Response:**
- `contract_id (bytes[32])`: Unique contract identifier (e.g. Keccak256).

---

#### `Invoke(InvokeRequest) → InvokeResponse`

Invokes a method within a deployed WASM contract, executing it in a transactional context.

**Request:**
- `signer (bytes)`: Public key or address of the invoker.
- `signature (bytes)`: Signature over the invoke payload.
- `contract_id (bytes[32])`: Contract ID obtained from deployment.
- `method (string)`: Exported WASM function to call.
- `input (bytes)`: ABI-encoded arguments.
- `gas_limit (uint64)`: Optional execution cap.

**Response:**
- `return_value (bytes[32])`: ABI-encoded result (e.g., a `u256`).

---

## Example Usage

### Deploy

```bash
grpcurl -plaintext \
  -d '{
        "signer": "...",
        "signature": "...",
        "wasm_code": "...",
        "contract_name": "counter"
      }' \
  localhost:50051 wastm.WASTMExecutor/Deploy
```

### Invoke

```bash
grpcurl -plaintext \
  -d '{
        "signer": "...",
        "signature": "...",
        "contract_id": "...",
        "method": "increment",
        "input": "...",
        "gas_limit": 1000000
      }' \
  localhost:50051 wastm.WASTMExecutor/Invoke
```

---

## ABI Encoding

Return values and input arguments must be ABI-encoded using the expected scheme (e.g., Ethereum-style for `u256`, structs, etc). The protocol itself is transport-agnostic and does not assume any particular ABI format, but clients and WASM contracts must agree on a shared encoding convention.

---

## Security Model

- All requests are authenticated using signed payloads.
- Replays are mitigated by including payload-derived context in the signature.
- The executor may later be extended to support additional on-chain verification (e.g., Merkle proofs, sequencer signatures).

---

## License

MIT. Provided as a reference implementation for the WASTM runtime and related tooling.