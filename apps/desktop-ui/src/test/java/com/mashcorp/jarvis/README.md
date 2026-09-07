# Desktop Contract Tests

`GatewayClientTest` uses JUnit and an ephemeral loopback `HttpServer`. It verifies
remote-host rejection, session-token validation, JSON-object options, typed
paper-command fields and HTTP failures. The server is fake: no Python trading
process or broker is contacted. Run with Maven `test` or `package`.

`GraphRenderPlanTest` verifies bounded, deterministic open-ring geometry, the
top opening, centered current decision, and immediate summary fields.
`ClientLatencyMonitorTest` verifies bounded samples and normalized dynamic
routes.
`HumanReadableFormatterTest` verifies typed operator formatting, nested
evidence sections and secret redaction.

Visual QA is separate: `DesktopSmokeCheck` renders views without operating any
buttons. Python authentication/settings/result tests remain in
`tests/test_dashboard.py` at the repository root.
