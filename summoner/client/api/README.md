# The Summoner Substrate: An Architectural Overview

## Introduction

Summoner is not a conventional framework but a foundational substrate. It is built on ruthlessly enforced principles designed to solve two problems in complex AI systems:

* How to model the state of the world
* How to record its history

This is achieved through two storage layers:

* **BOSS**: an object-associative knowledge graph
* **Fathom**: an immutable sequential ledger

Understanding this separation is central to mastering the platform.

---

## The Two Pillars of the Substrate

### 1. BOSS (Basic Object Storage Substrate)

**Purpose**: Models the current state of the world.
**Design**: Expressive, graph-based, durable, and predictable.

Core concepts:

* **Versioned Objects**: Nouns of the system (e.g., AI Agent, User Account).
* **Structural Associations**: Verbs connecting objects (e.g., `monitors_model`).

Key property:

* **Automatic optimistic locking** ensures data integrity in high-concurrency environments.

Superpower:

* **Modeling Without Migrations**: Introduce new concepts or relationships by creating new objects and associations. Most data modeling changes require no schema migrations or new endpoints. This enables rapid modeling at the speed of thought.

---

### 2. Fathom (The Immutable Ledger)

**Purpose**: Records history with perfect integrity.
**Design**: High-throughput, append-only, reliable.

Properties:

* **Shock Absorber**: Internal batching system smooths chaotic, concurrent writes into efficient, predictable transactions.
* **Perfect Audit Trail**: Immutable by design, enabling trustworthy audit logs for guardrails, agent decisions, or model compositions.

---

## Identity as Demonstration

Identity is the first application built on the substrate, proving its flexibility.

* **Account as an Object**: A user’s identity is modeled as a first-class Account object in BOSS. The endpoint `/api/account/me` resolves a temporary JWT into this permanent node.
* **Valet Key Pattern**: Uses a **Principal Secret**. A user can provision a secret for an agent, allowing it to authenticate without exposing primary credentials. This enables clean, secure automation.

---

## The Contract with the Substrate

* **Correctness Guaranteed**: Transactional integrity and versioning ensure consistent, incorruptible data.
* **Client Responsible for Performance**: No server-side caching. Clients must implement their own caching strategies tailored to their application.
* **Self-Proving System**: On startup, the platform executes a full pre-flight check. The system runs only if fully operational; partial or degraded modes are disallowed.

---

## Conclusion

The Summoner substrate provides two unified layers—BOSS for state, Fathom for history—that together deliver a transparent, expressive, and auditable foundation for AI systems. By internalizing these principles, developers can build robust, scalable, and verifiable architectures.
