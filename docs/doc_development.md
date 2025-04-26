# 🔐 GitHub Branch Rulesets

To maintain a secure, reliable, and high-quality codebase, the `Summoner-Network/agent-sdk` repository enforces branch protection rules on both the `main` and `dev` branches.  
Please review the following guidelines carefully before contributing.

## 🚩 `ruleset-main` (Default Branch)

> **Branch**: `main`  
> **Purpose**: Protect the integrity of production-ready code.

The `main` branch is reserved for stable and validated updates. Changes must pass through a strict review and security process.

### Enforced Rules:
- **🔒 No branch deletion** — The `main` branch cannot be deleted.
- **🚫 No non-fast-forward pushes** — History must remain linear and auditable.
- **📥 Pull Requests Only** — Direct pushes are not allowed.  
  Each pull request must:
  - Receive at least **2 approving reviews**.
  - Include a **code owner** review.
  - Receive approval **on the latest push**.
  - Resolve **all review threads** before merging.
  - Allow **merge**, **squash**, or **rebase** methods.
- **🛡️ Code Scanning (Security)** — Automated security scanning with `CodeQL` is enforced:
  - **Security alerts** must be resolved at `high` severity or higher.
  - **General alerts** must be resolved if classified as `errors`.
- **🔒 No direct pushes or branch creation** — Only through approved pull requests or admin bypass.


## 🧪 `ruleset-dev` (Development Branch)

> **Branch**: `dev`  
> **Purpose**: Maintain quality during active development.

The `dev` branch supports ongoing feature work while ensuring a minimum level of code review and security compliance.

### Enforced Rules:
- **🔒 No branch deletion** — The `dev` branch cannot be deleted.
- **🚫 No non-fast-forward pushes** — Linear history must be maintained.
- **📥 Pull Requests Only** — Direct pushes are not allowed.  
  Each pull request must:
  - Receive at least **1 approving review**.
  - Receive approval **on the latest push**.
  - Resolve **all review threads** before merging.
  - Allow **merge**, **squash**, or **rebase** methods.
- **🛡️ Code Scanning (Security)** — CodeQL scanning is also enforced:
  - **Security alerts** must be resolved at `high` severity or higher.
  - **General alerts** must be resolved if classified as `errors`.

## 📌 Important Notes for All Contributors

- Always open a **pull request** when proposing changes.  
  **Direct pushes** to `main` or `dev` are not permitted.
- Ensure that your changes pass the **required number of reviews** based on the branch.
- Resolve any **CodeQL alerts** before merging.
- If you encounter any questions about these rules, please open an issue or start a discussion.

Following these policies helps keep our codebase clean, secure, and collaborative!
