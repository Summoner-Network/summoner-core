# WASTM gRPC Interface & Protocol Documentation

WASTM (WebAssembly Software Transactional Memory) provides a deterministic, secure, and concurrent execution environment for WebAssembly smart contracts. This gRPC protocol supports deploying WASM contracts and invoking contract methods in a batched manner with built-in replay protection and integrity checks.

---

## Table of Contents

- [Overview](#overview)
- [Service Definition](#service-definition)
- [Message Specifications](#message-specifications)
  - [DeployRequest & DeployResponse](#deployrequest--deployresponse)
  - [InvokeRequest, InvokeRequestInner, InvokeResponse & InvokeReturn](#invokerequest--invokerequestinner-invokeresponse--invokereturn)
- [Security and Replay Protection](#security-and-replay-protection)
- [Usage Examples](#usage-examples)
- [License](#license)

---

## Overview

The **WASTMExecutor** service defines two core operations:

1. **Deploy** – Upload and register a WASM contract, with an integrity hash for the deployment request.  
2. **Invoke** – Execute one or more methods on deployed contracts by batching individual invocation calls. Each call includes a nonce and hash to prevent replay and to ensure request integrity.

This design provides a secure and efficient interface for contract deployment and batched invocation in WebAssembly-based smart contract systems.

---

## Service Definition

The service is defined using Protocol Buffers version 3 (`proto3`) under the `wastm` package. The two main RPC endpoints are:

- **Deploy(DeployRequest) → DeployResponse**  
  Deploy a signed WASM contract into persistent state.

- **Invoke(InvokeRequest) → InvokeResponse**  
  Invoke one or more methods on deployed contracts as a single batched request.

---

## Message Specifications

### DeployRequest & DeployResponse

The **DeployRequest** message carries all necessary information to deploy a WASM contract, including an asserted hash for request integrity.

- **signer (bytes)**: Public key or address of the deployer.
- **signature (bytes)**: Cryptographic signature over the deploy payload.
- **wasm_code (bytes)**: The raw WASM module to be deployed.
- **contract_name (string)**: A human-readable identifier for the contract.
- **hash (bytes)**: An asserted hash of the complete deploy request (used to verify integrity).

The **DeployResponse** returns:

- **contract_id (bytes)**: A 32-byte identifier (typically a hash) uniquely representing the deployed contract.

### InvokeRequest, InvokeRequestInner, InvokeResponse & InvokeReturn

The **InvokeRequest** message batches one or more invocation calls into a single RPC.

Each inner call (**InvokeRequestInner**) contains:

- **signer (bytes)**: Public key or address of the caller.
- **signature (bytes)**: Signature over the inner payload.
- **contract_id (bytes)**: The target contract’s 32-byte identifier.
- **method (string)**: The name of the method to invoke.
- **input (bytes)**: ABI-encoded input payload for the method.
- **gas_limit (uint64)**: An optional cap on the gas that may be consumed.
- **nonce (uint64)**: A unique per-signer value that prevents replay attacks.
- **hash (bytes)**: An asserted hash of the inner invoke request, ensuring integrity.

The **InvokeResponse** message provides the results for each inner call:

- **returns (repeated InvokeReturn)**: A list of response entries corresponding to each invocation.

Each **InvokeReturn** includes:

- **return_value (bytes)**: The ABI-encoded output from the invoked method (typically a 32-byte value).

---

## Security and Replay Protection

This protocol integrates multiple mechanisms to ensure secure and deterministic execution:

- **Signatures:**  
  Every request (deploy or invoke) includes a cryptographic signature that authenticates the sender.

- **Nonces:**  
  Each **InvokeRequestInner** contains a monotonically increasing nonce per signer. This measure prevents replay attacks by ensuring that an invocation cannot be processed more than once.

- **Hash Fields:**  
  Both the **DeployRequest** and **InvokeRequestInner** messages include a `hash` field. This asserted hash (which should be calculated over the complete request payload) provides an integrity check to detect any tampering with the request data.

---

## Usage Examples

### Deploying a Contract

Use a gRPC client (such as `grpcurl`) to deploy a WASM contract:

```bash
grpcurl -plaintext \
  -d '{
        "signer": "base64-encoded-signer",
        "signature": "base64-encoded-signature",
        "wasm_code": "base64-encoded-wasm",
        "contract_name": "MyContract",
        "hash": "base64-encoded-hash"
      }' \
  localhost:50051 wastm.WASTMExecutor/Deploy
