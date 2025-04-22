# Summoner's SDK

### Current status

- Handles reconnection, shutdown, and custom logic ✅
- Is robust for development / testing ✅
- But still needs a few things for production 👇

### To become production-ready:

| Requirement | Status | Needed? |
|-------------|--------|---------|
| **TLS/SSL** | ❌ | Yes, for WAN use |
| **Auth & Identity** | ❌ | Needed for real-world sessions |
| **Error logging** | ⚠️ Minimal | Use `logging` module w/ levels |
| **Structured logs or metrics** | ❌ | Optional: Prometheus or JSON logs |
| **Auto-restart / service** | ❌ | Systemd, Docker, or Supervisor |
| **Testing** | ⚠️ Manual | Add `pytest` + test harnesses |
| **Deployment config** | ❌ | e.g., Dockerfile, Makefile |
| **Versioning of protocol** | ❌ | Important for future upgrades |

### To have WAN connections:

| Requirement | Status |
|------------|--------|
| TCP-based | ✅ You’re already using `asyncio.open_connection()` and `start_server()` over IP/port |
| Local/remote flexibility | ✅ Just use public IPs or DNS (e.g., `await asyncio.open_connection("myserver.com", 8888)`) |
| Port forwarding / firewall config | ⚠️ Needed for server on WAN (like Minecraft) |
| TLS encryption (optional) | ❌ Not yet — needed for security over WAN |
| Authentication | ❌ Not yet — needed for open networks |

System will **work as a WAN agent party** with:
- Remote IPs
- Basic network config (firewalls, NAT)
- (Optional but recommended): add TLS and identity/auth flows.

