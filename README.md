# Agent SDK · Summoner Platform

<p align="center">
<img width="200px" src="img/92a3447d-6925-431e-a2d0-a1ee671cd9bd.png" />
</p>

> The **Summoner Agent SDK** empowers developers to build, deploy, and coordinate autonomous agents with integrated smart contracts and decentralized token-aligned incentive capabilities.

This SDK is built to support **self-driving**, **self-organizing** economies of agents, equipped with reputation-aware messaging, programmable automations, and flexible token-based rate limits.

## Installation

```sh
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Preparation

Set up `.env`:
```sh
# .env
LOG_LEVEL=INFO
ENABLE_CONSOLE_LOG=true
DATABASE_URL=postgres://user:pass@localhost:5432/mydb
SECRET_KEY=supersecret
```

Update or make sure `summoner/setting.py` accurately translates your setup:
```python
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG")
ENABLE_CONSOLE_LOG = os.getenv("ENABLE_CONSOLE_LOG", "true").lower() == "true"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///local.db")
SECRET_KEY = os.getenv("SECRET_KEY", "devsecret")
```

## Run server and clients

In terminal 1:
```
python user_space/myserver.py
```

In terminal 2:
```
python user_space/myclient.py
```

In terminal 3:
```
python user_space/myclient.py
```

Try to talk or shutdown the server / clients (clean shutdown integrated)

---

## 🔐 GitHub Branch Rulesets

To maintain a high-quality and secure codebase, we enforce different rulesets on our main and development branches in the `Summoner-Network/agent-sdk` repository. Please follow the policies below when contributing.

---

### 🚩 `ruleset-main` (Default Branch)

> **Target**: Default branch (`main`)  
> **Status**: ✅ Active  
> **Purpose**: Protect the integrity of production-ready code

#### ✅ Enforced Rules:
- **🔒 No branch deletion**
- **🚫 No non-fast-forward pushes**
- **📥 Pull Requests Only**
  - Minimum **2 approving reviews**
  - **Code owner** review required
  - **Approval required** on the **last push**
  - **Dismiss stale reviews** on new commits
  - **All review threads must be resolved**
  - **Allowed merge methods**: Merge, Squash, Rebase
- **🛡️ Code Scanning (Security)**
  - Tool: `CodeQL`
  - Thresholds: 
    - Security Alerts: `high_or_higher`
    - General Alerts: `errors`
- **✅ Allow branch creation and updates**

---

### 🧪 `ruleset-dev` (Development Branch)

> **Target**: `refs/heads/dev`  
> **Status**: ✅ Active  
> **Purpose**: Ensure quality during ongoing development

#### ✅ Enforced Rules:
- **🔒 No branch deletion**
- **🚫 No non-fast-forward pushes**
- **📥 Pull Requests Only**
  - Minimum **1 approving review**
  - **Approval required** on the **last push**
  - **All review threads must be resolved**
  - **Allowed merge methods**: Merge, Squash, Rebase
- **🛡️ Code Scanning (Security)**
  - Tool: `CodeQL`
  - Thresholds: 
    - Security Alerts: `high_or_higher`
    - General Alerts: `errors`

---

### 📌 Note for Contributors

- Always use **Pull Requests** for changes—**no direct pushes** to `main` or `dev`.
- Follow the required review processes per branch.
- Address all **code scanning issues** before merging.

Let us know in issues or discussions if you have questions about the rules!

---

Would you like this written into a `README`, `CONTRIBUTING.md`, or a separate `rulesets.md` file?
