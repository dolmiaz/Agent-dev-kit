# Agent-Dev-Kit

[日本語版 (Japanese)](README.ja.md)

Agent-Dev-Kit provides shared setup, runtime environment management, work-log processing, issue-to-merge automation, skill management, and publication safety gates for coding agents operating in cloud environments.

## Target Environments and Scope

- **Supported Environments:** Codex Cloud and Claude Code on the web.
- **Scope:** The kit manages setup, dependency inspection, local identity, work logs, issue workflow, skill vendoring, and publication gates. Local helper scripts support configuration and log management.
- **Out of Scope:** The kit does not configure Codex Legacy, local coding-agent CLIs, or Remote Control.

## Core Features

### 1. Installation, Update, and Removal
- `install.py` manages installation of shared tools into `.agent-kit/`, service-specific hooks, instruction files, agent skills, and optional GitHub templates (`--github-templates`).
- Supports `--dry-run`, `--only codex`, `--only claude`, and `--uninstall` flags.
- Uses a manifest file to track managed files. Backups are created outside the target repository to preserve modified user files.
- Rejects unsafe symlinks and out-of-scope manifest paths prior to any mutations.
- Setting configuration via `--profile <file>` parses recognized `AGENT_` assignments and merges them into `.agent-kit/config.env` (replacing existing values or appending new ones). Because `.agent-kit/config.env` is normally tracked by Git, it must never contain secrets.

### 2. Configuration & Common Runtime Support
- Detects runtimes and services, reads non-secret `AGENT_` configuration variables, maintains checkout-specific state, fingerprints inputs, and handles locks, timeouts, and bounded retries.
- **Ordinary Configuration Hierarchy:** Environment variables > `.agent-kit/config.env` > system defaults.
- **Publication Configuration Hierarchy:** Environment variables > `~/.config/agent-kit/publish.env` > `.agent-kit/config.env` > system defaults. (The user-local `publish.env` stores non-secret publication parameters, not API secrets).

### 3. Dependency Preparation & Inspection
- `setup-deps.sh` supports commands: `deps`, `extras`, `all`, `check`, and `plan` across Node.js, Python, Go, Rust, Ruby, PHP, Gradle, and Maven.
- Uses frozen or locked installations when available. Never rewrites lockfiles; skips unsupported unlocked environments.
- Evaluates both input fingerprints and installed artifacts before bypassing repeat setups. Playwright dependencies are installed only when detected or requested.
- `check` and `plan` inspect project files without running package installers; however, they are not completely read-only as the underlying lock runtime may create external state directories and state lockfiles.
- Failures default to non-blocking behavior unless strict mode is explicitly enabled.

### 4. Optional Service Lifecycle Management
- `services.sh` can start, stop, and report status for local PostgreSQL and Redis services when binaries are present.
- *Note:* This is local service management, not a cloud managed database service.

### 5. Repository-Local Agent Identity
- `git-identity.sh` sets repository-scoped commit author names and email addresses.
- Does not modify global Git configuration, manage signing keys, or grant repository push privileges.

### 6. Agent Integration and Permissions
- Provides setup entry points and instruction blocks for Codex.
- Provides environment setup, session-start, turn-start, stop, and pre-tool hooks for Claude.
- Applies cloud permission rules and publication checks within supported cloud contexts without adding cloud permissions to local Claude instances. Repository-specific restrictions take precedence. Hooks do not guarantee universal interception of all execution or publication paths.

### 7. Work-Log Creation & Delivery
- `agent-log` supports `turn`, `sync`, `status`, `show`, and `redact` subcommands.
- Generates Markdown work logs, redacts common secret patterns, assigns UUIDs, and verifies delivery via unique IDs and content hashes.
- Retains pending logs locally upon delivery failure. Users must supply their own log destinations and credentials; no default third-party log endpoint is provided.

### 8. Write-Only Drive Receiver
- An Apps Script endpoint accepts authenticated Markdown logs for Codex and Claude.
- Verifies agent service type, filenames, UUIDs, metadata, file sizes, and content hashes.
- Handles duplicate requests, enforces denied folder rules and parent ancestry, and applies rate, ledger, and storage limits.
- Requires user-deployed infrastructure, specific destination settings, and dedicated service keys. Does not grant general Drive read access or install Obsidian.

### 9. Issue Eligibility & Ownership Management
- `agent-gh` lists eligible GitHub issues, evaluates dependency trees, claims issues, manages claim leases/renewals, and facilitates handoffs, takeovers, or ownership adoption.
- Claim leases limit duplicate concurrent work. Supports label configuration and draft PR creation.

### 10. Independent Review & Merge Assessment
- `agent-gh` records reviews against fixed commit SHAs, validates model and session execution evidence, logs blocking issues, and accepts signed human confirmations.
- Evaluates base-branch CI status, review requirements, and mergeability policies. Unknown evidence is not treated as a passing state.
- Assesses merge readiness; does not perform automatic PR merges.

### 11. Required Gemini Publication Gate
- `agent-publish` controls PR titles/bodies, release notes, and public documentation updates across managed routes and configured public document patterns (`AGENT_PUBLIC_DOC_GLOBS`).
- Pre-scans drafts before network transmission, executing a configured Gemini command within an isolated working directory.
- Disables local `.env` loading and CLI tools in its isolated configuration, while explicitly passing a restricted allowlist of environment variables and required authentication credentials (such as `GEMINI_API_KEY` in API-key mode) to the child process.
- Validates reported model usage, tool execution, and protected content in responses. Hash records link drafts to final outputs.
- Subcommands cover `polish`, `retry`, `revise`, `show`/`list`, `reviewed-diff` confirmation, and documentation application.
- Gate enforcement requires explicitly configured settings, authentication, valid quota, and verified CLI versions. Codex relies on instruction compliance, while Claude uses cloud hooks; neither guarantees interception of all unmanaged publication attempts.

### 12. Skill Inventory & Distribution
- `skillsync` provides inventory tracking, source bundle exports, set-based skill vendoring, verification, rollback, recovery, and zip packaging.
- Maintains tracking manifests and lockfiles, rejects secret-bearing content, and performs public target checks. Does not export unmanaged personal skill collections by default.

### 13. Public Export Checks
- `make-public.sh` creates a fresh public repository copy using an allowlist.
- `publish-check.py` scans target directories for credentials, personal information, disallowed paths, symbolic links, and uninspected binary files.
- History inspection is optional but mandatory when exporting Git history; unreadable or shallow history triggers a safe failure (fail-closed). Identified secrets are redacted. Clean scan output does not constitute a absolute guarantee of security.

### 14. Testing & Documentation
- Includes shell regression suites, installer safety tests, and mocked workflows for Apps Script, GitHub, Drive delivery, and Gemini operations.
- Shell test suites require Linux/GNU core utility environments. Selected mocks rely on a local HTTP server.
- Setting `NO_NET=1` skips external dependency downloads during testing; it does not block local network loopback operations used by test mocks.
- Provided under the MIT License with separate third-party notices.

---

### Implemented Development-Branch Additions (Not in `main`)

1. **Tracked Blank Key Template**: `.env.sample` contains a blank `GEMINI_API_KEY=` assignment and is explicitly tracked, while `.env` remains ignored.
2. **Safe Local Key Launcher**: `local/with-gemini-env.py` safely parses local `.env` assignments (requiring strict 600/400 permissions) and passes `GEMINI_API_KEY` to `agent-publish` via process environment. Its `--check` flag verifies a valid non-empty assignment.
3. **Secret-Safe Gemini Error Handling**: Output containing echoed API credentials is discarded, and safe error categories/exit codes are reported instead of raw excerpts.
4. **Local Test Coverage**: Added local-env and public-export tests (covering blank/populated samples and deleted history) along with updated CI wiring.

## Setup and Usage Guide

### Installation
1. Perform a dry-run preview:
   ```bash
   python3 install.py /path/to/target-repo --dry-run --github-templates
   ```
2. Run the actual installation:
   ```bash
   python3 install.py /path/to/target-repo --github-templates
   ```

### Non-Secret Publication Configuration
Configure non-secret publication parameters in environment variables, `~/.config/agent-kit/publish.env`, or `.agent-kit/config.env`:
```env
AGENT_GEMINI_SEND_APPROVED=1
AGENT_GEMINI_AUTH=api-key
AGENT_GEMINI_KEY_ENV=GEMINI_API_KEY
AGENT_GEMINI_MODEL=your-supported-model
AGENT_GEMINI_CLI_VERSION=your-verified-version
AGENT_GEMINI_HOME=/path/to/isolated/gemini-home
```
*Do not place API secret values inside these configuration files.*

### Executing the Publication Workflow
1. Run the polish command with the API key present in the environment:
   ```bash
   # Installed target entry point:
   python3 .agent-kit/bin/agent-publish polish --kind readme --file README.md --path README.md
   ```
2. Review the resulting draft diff, confirm the changes, and apply:
   ```bash
   python3 .agent-kit/bin/agent-publish confirm <ID> --reviewed-diff
   python3 .agent-kit/bin/agent-publish apply <ID>
   ```

### Performing Public Export Safety Scans
1. Generate a public export to a new or empty target directory:
   ```bash
   bash tools/make-public.sh /path/to/empty-export-dir
   ```
2. Run the security scanner:
   ```bash
   python3 tools/publish-check.py /path/to/empty-export-dir
   ```

### Documentation Index
- `docs/01-codex-cloud-settings.md`: Codex Cloud configuration settings.
- `docs/02-claude-code-web-settings.md`: Claude Code on the web configuration settings.
- `docs/03-network-packages-permissions.md`: Network and package permission management.
- `docs/04-identity-signing-secrets.md`: Git identity and credential isolation practices.
- `docs/05-worklog-to-obsidian.md`: Setting up work-log delivery to Drive/Obsidian.
- `docs/06-manual-setup.md`: Manual kit installation and bootstrap instructions.
- `docs/08-skills-distribution.md`: Agent skill inventory and distribution guidelines.
- `docs/09-issue-to-merge-workflow.md`: End-to-end issue claiming, review, and merge-check workflow.
- `docs/10-publish-polish.md`: Gemini publication gate commands and workflow.
- `docs/sources.md`: Technical reference for repository components.
