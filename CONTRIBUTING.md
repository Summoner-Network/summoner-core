# CONTRIBUTING

## Scope

This repository is open source for visibility and usage.

External developers are welcome to open issues to report bugs, suggest improvements, or request new features.

Direct code contributions to this repository are limited to internal team members.

If you would like to extend the project, refer to the Summoner documentation on building modules and use the SDK template repository as a starting point for creating safe, compatible extensions.

## Reporting issues

Use GitHub Issues for:

- bug reports
- feature requests
- documentation issues
- questions about expected behavior

When filing an issue, include:

- a short description of the problem or request
- steps to reproduce, if applicable
- expected behavior and actual behavior
- relevant logs, stack traces, and environment details
  - OS and version
  - Python version
  - Rust toolchain version, if applicable
  - whether you used `setup.sh --uv` or the default setup

## Pull requests from external contributors

This repository does not accept direct code contributions from external contributors.

However, for demonstration purposes, external developers may still open a pull request and link it from an issue to illustrate a proposed change. This is useful as a concrete reference for discussion, review, or future internal implementation.

If you do this:

- open an issue first, or link the pull request to an existing issue
- keep the pull request small and focused on one change
- explain the motivation and tradeoffs in the pull request description
- expect that the pull request may not be merged, even if the change is accepted conceptually

## Branching and best practice

If you create a branch for a demonstration pull request, create it from `dev`, not `main`.

- `dev` is the integration branch for ongoing work.
- `main` is treated as a higher-bar stability branch.

In practice:

- base your branch on `dev`
- target your pull request to `dev`

## Review and merge policy

This section describes the repository's merge requirements for maintainers and internal team members. It is included for transparency so external contributors can understand the review gates applied to changes.

Branch protection and review requirements are enforced.

- On `dev`, merges require:
  - at least 1 approving review
  - CODEOWNERS review
  - resolution of review threads
  - approval of the last push

- On `main`, merges require:
  - at least 2 approving reviews
  - CODEOWNERS review
  - resolution of review threads
  - approval of the last push

Code scanning is enforced via CodeQL with high-severity thresholds.

Merge methods may include merge commits, squash merges, or rebases, depending on repository settings.

## Extensions and downstream development

If you want to build on Summoner without modifying this repository:

- use the SDK template repository as the starting point
- implement features as modules/extensions in your own repository
- follow the documentation for compatibility and safe integration patterns

This approach keeps the core SDK stable while allowing downstream experimentation and custom features.
