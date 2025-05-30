# Summoner core SDK

<p align="center">
<img width="250px" src="img/92a3447d-6925-431e-a2d0-a1ee671cd9bd.png" />
</p>

> The **Summoner core SDK** empowers developers to build, deploy, and coordinate autonomous agents. In the future, native libraries around our core SDK will help developers integrate smart contracts and decentralized token-aligned incentive capabilities.

This core SDK is built to support **self-driving**, **self-organizing** economies of agents, equipped with reputation-aware messaging, programmable automations, and flexible token-based rate limits.

## 📚 Documentation

Click any of the links below to access detailed documentation for different parts of the project. Each document contains focused guidance depending on what you want to work on or learn about.

- ✅ **[Installation Guide](docs/doc_installation.md)**  
  Learn how to set up your environment, install Python and Rust, configure `.env` variables, and run the `setup.sh` script to prepare your system for development.

- ✅ **[Creating an Agent](docs/doc_make_an_agent.md)**  
  Learn how to build and configure a custom agent using the core SDK. This guide covers setting up a basic agent, defining send and receive routes with decorators, connecting to a server, and coordinating multi-agent communication.

- ✅ **[Contributing to the Rust Server Codebase](docs/doc_contribute_to_server.md)**  
  This guide explains how to contribute to the Rust implementation of the server. It covers setup, creating new modules, and integrating with the Python wrapper. Ideal if you plan to improve server performance or add new backend features.

- ✅ **[Development Guidelines](docs/doc_development.md)**  
  Learn the coding practices, pull request workflows, and security policies for contributing to the repository. This guide explains how to work with the `dev` and `main` branches and maintain code quality across the project.

## Advanced Examples

<p align="center">
<img width="180px" src="img/merchants.png" />
</p>

- ✅ **[Simulating Autonomous Negotiations Between Agents](examples/3_buyer_seller_agents/)**  
  Explore a complete example where two agents negotiate dynamically over a shared resource. This guide demonstrates how to implement stateful decision-making, strategic counteroffers, and termination conditions, showcasing more advanced agent behaviors beyond basic message passing.
