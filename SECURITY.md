# Security Policy

Dhee is local-first infrastructure that can see prompts, tool output, repo
context, memory, artifacts, and agent handoffs. Treat it as part of the trusted
developer workstation.

## Supported Versions

Security fixes target the current public release line and `main`.

## Reporting a Vulnerability

Do not open a public issue for suspected vulnerabilities involving secrets,
prompt injection payloads, private paths, private repo content, or bypasses.

Report privately through GitHub Security Advisories for this repository. Include:

- Dhee version or commit SHA.
- Operating system and Python version.
- Installed harnesses involved: Claude Code, Codex, Hermes, or MCP.
- Reproduction steps with secrets replaced by placeholders.
- Expected vs actual trust-boundary behavior.

## Trust Boundaries

Dhee has five main boundaries:

- **Local data root:** personal state under `~/.dhee` stays local unless the user exports or syncs it.
- **Repo-shared context:** `<repo>/.dhee/context` is git-tracked and must never contain secrets or bulk private data.
- **Router pointer store:** raw `Read`, `Bash`, `Grep`, and agent outputs stay behind local pointers until explicitly expanded.
- **`.dheemem` packs:** portable archives are signed and validated before import.
- **Runtime daemon:** loopback-only by default; daemon-backed bash requires explicit server-side opt-in and cwd allowlisting.

## Threat Model

Dhee actively defends against:

- Secret leakage from hook-captured tool output.
- Prompt injection stored in repo-shared context.
- Symlink escape from repo context and pack import paths.
- Archive traversal and tampered `.dheemem` manifests.
- Stale context being injected after task drift.
- Oversized tool output flooding the model context.
- Native bash acceleration outside approved daemon trust boundaries.

Dhee does not claim to defend against:

- A compromised developer account or workstation.
- A malicious LLM provider receiving content the user intentionally sends.
- Unsafe commands the user or agent explicitly runs outside Dhee routing.
- Public network exposure of local services without a trusted auth proxy.

## Security Controls

- Secret filtering is applied before hook-derived memory and replay-corpus storage.
- Repo-shared context import rejects symlinked files, traversal paths, and likely secrets.
- Router digests expose summaries first and require `dhee_expand_result(ptr)` for raw evidence.
- Context state tracks epochs, stale assertions, duplicate reads, and context debt.
- Runtime daemon binds to loopback and refuses public hosts unless explicitly overridden.
- `dhee uninstall` stops the daemon and removes Dhee-owned harness wiring, symlinks, shell PATH blocks, and managed runtime data.

## Team Governance Controls

A paid/team governance layer should be used when teams need:

- Org/team dashboards.
- Policy and approval workflows.
- Audit export.
- SSO/RBAC.
- Context-manager findings.
- Cross-team governance for promoted learnings and shared repo context.

The public MIT package remains complete for local developer use; paid team
features should add governance, not basic functionality.

## Handling Secrets

Do not promote secrets into memory, repo-shared context, or `.dheemem` packs.
Use provider environment variables or Dhee's local encrypted secret store for API
keys. If a secret is accidentally captured, rotate it first, then purge the
affected memory/context/artifact records.

## Security Philosophy

Memory without governance becomes prompt pollution. Dhee's job is not to make
agents remember everything. Dhee's job is to make context admission explicit,
auditable, minimal, and reversible.
