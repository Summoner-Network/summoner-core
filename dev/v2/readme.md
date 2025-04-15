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

