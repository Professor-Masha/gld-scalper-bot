# JARVIS Control-Plane Implementation

Copyright (c) @Mashcorp. All rights reserved.

## Decision

The Parts I-VII technical design document is the target architecture. The
current implementation preserves its control-plane contracts in Python/FastAPI
and replaces the primary Windows presentation layer with a JavaFX client.

This is an intentional architecture decision:

- The Python engine remains the only trading authority.
- The JavaFX interface is a local operator shell; browser assets are fallback.
- FastAPI is the versioned gateway between presentation and engine commands.
- Every material command still invokes an allowlisted `gld_scalper.main`
  command and therefore retains the existing risk, reconciliation, journal,
  idempotency, and shutdown behavior.
- JavaFX consumes `/api/v1` and `/api/v1/events` without changing the engine or
  creating another broker path.

## Implemented From The Design

### Versioned Gateway

The gateway exposes stable `/api/v1` resources for health, readiness, system
state, GLD market state, account state, positions, orders, decisions, models,
training jobs, commands, and audit evidence. Existing `/api` routes remain as a
compatibility layer for the installed dashboard.

### Typed Commands

The control plane accepts only four command families:

1. `bot.start`
2. `bot.stop`
3. `job.start`
4. `job.stop`

Job actions remain constrained by the existing dashboard allowlist. No raw
shell command, broker method, arbitrary module, or filesystem path can be sent
from a presentation client.

Every command has a command ID, actor, request time, idempotency key,
correlation ID, typed parameters, status, and structured result. Repeating the
same idempotency key returns the previous result instead of launching another
process.

### Event Envelope

`/api/v1/events` emits a versioned event envelope:

```json
{
  "event_id": "evt-...",
  "event_type": "system.snapshot",
  "version": 1,
  "timestamp": "ISO-8601",
  "trace_id": "trace-...",
  "source": "gld-dashboard-gateway",
  "symbol": "GLD",
  "sequence": 42,
  "payload": {}
}
```

JavaFX uses the sequence number to distinguish event transport from the
state payload. The older `/ws/live` stream is retained temporarily for backward
compatibility.

### Health And Readiness

Liveness and readiness are separate:

- Health confirms that the local gateway and audit subsystem are operational.
- Readiness checks the SQLite telemetry path, audit integrity, paper-mode lock,
  and command allowlist.
- The displayed UI state is derived from backend process and persistence truth.
  Clicking Start cannot itself make the UI claim the bot is running.

### Tamper-Evident Audit

Material dashboard actions are appended to
`logs/dashboard/control_plane_audit.jsonl`. Each record contains the previous
record hash and its own SHA-256 hash. The Control Plane view verifies the chain
and displays recent commands with sequence, action, actor, status, and
correlation ID.

Secret-like fields are recursively redacted before serialization. This audit
ledger supplements the SQLite trading journal and broker reconciliation; it
does not replace either one.

### Interface Infrastructure

The interface now includes:

- top-ribbon gateway state and API version;
- a Control Plane workstation;
- health, readiness, authority, and audit-integrity status;
- readiness checks and versioned gateway contract details;
- managed asynchronous job state;
- the tamper-evident operator command ledger;
- native allowlisted job selection (the legacy browser retains its `Ctrl+K` palette);
- correlation IDs on HTTP responses;
- responsive behavior and textual status in addition to color.

## Security Boundary

The server remains localhost-only. Mutations require the random dashboard
token. WebSocket authentication is sent in the first frame, not the URL.
Commands are rate limited, serialized, idempotent, and audited. Credentials are
never returned to presentation clients or written into audit details. LLMs, 3D scenes,
charts, command-palette labels, and natural-language output have no broker
authority.

## JavaFX Desktop Boundary

`apps/desktop-ui` is a Java 21/JavaFX 21 client. `GatewayRuntime` chooses an
unused loopback port, creates a 256-bit token, starts the Python gateway with
that token in the child environment, and waits for health. `GatewayClient`
authenticates typed mutations and consumes versioned event envelopes. The
native 3D decision core, charts, forms, and tables project backend truth; they
do not read SQLite or load Alpaca libraries.

The Desktop shortcut launches JavaFX. The project-local JDK and Maven runtime
are downloaded to ignored `.tools/`, so no system-wide Java installation is
required and tool binaries are not committed.

## Deliberately Deferred

The following design-document items are not falsely represented as complete:

- signed MSI packaging and automatic JavaFX updates;
- PostgreSQL, Redis, object storage, and multi-service deployment;
- multi-user authentication and RBAC;
- voice control;
- live-mode credentials and live-trading approvals;
- autonomous order tools;
- multi-host service discovery;
- production TLS and remote network exposure.

They are appropriate only after the local paper system has stable clean
episodes, operational acceptance evidence, backups, migration plans, and a
separate production deployment environment.

## Extension Path

A future mobile or remote operations client should consume the same versioned
contracts. It must not call Alpaca directly. High-risk capabilities
must be added as new typed commands with approval records and deterministic
policy checks rather than widening the existing generic process endpoints.
