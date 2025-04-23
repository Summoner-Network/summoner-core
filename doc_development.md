
## 🔐 GitHub Branch Rulesets

To maintain a high-quality and secure codebase, we enforce different rulesets on our main and development branches in the `Summoner-Network/agent-sdk` repository. Please follow the policies below when contributing.

---

### 🚩 `ruleset-main` (Default Branch)

> **Target**: Default branch (`main`)  
> **Purpose**: Protect the integrity of production-ready code

#### Enforced Rules:
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
- **🔒 No direct pushes or branch creation allowed without bypass.**

---

### 🧪 `ruleset-dev` (Development Branch)

> **Target**: `refs/heads/dev`  
> **Purpose**: Ensure quality during ongoing development

#### Enforced Rules:
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

