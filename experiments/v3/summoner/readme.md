# Project Organization 

```
summoner/
├── client/
│   ├── __init__.py
│   ├── base.py         # Base AgentClient class
│   ├── decorators.py   # @on_message, @on_event, etc.
│   ├── runtime.py      # Session loop, asyncio logic
│   └── types.py        # MessageType, ClientConfig
│
├── server/
│   ├── __init__.py
│   ├── core.py         # Asyncio server startup, shutdown
│   ├── handlers.py     # handle_client, broadcast logic
│   └── state.py        # Client tracking, rooms, broadcast queues
│
├── protocol/
│   ├── __init__.py
│   ├── message.py      # Message class, JSON schema, validation
│   ├── commands.py     # /ping, /quit, /login logic
│   └── errors.py       # Custom exceptions: ClientDisconnected, ProtocolError
│
├── rust/
│   ├── relay/          # Rust async TCP server (e.g. using tokio)
│   ├── tokens/         # CAST token logic (Elliott's code)
│   └── ffi/            # Interface to expose methods to Python (via pyo3 or gRPC)
│
└── sdk.py              # Entrypoint to expose decorator tools
```