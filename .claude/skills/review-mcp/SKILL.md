---
name: review-mcp
description: Review MCP configuration, permissions, authentication, data egress, and prompt-injection exposure without connecting to servers.
disable-model-invocation: true
context: fork
agent: mcp-integrator
---

Review MCP configuration without authenticating or invoking external MCP tools. This repository ships no project-scope configuration, so the subject is whatever the developer has connected at user scope, or a configuration being proposed.

Check scope, transport, endpoint trust, dependency pinning, credentials, least privilege, write capabilities, production access, PII, prompt injection, timeout, auditability, managed-policy compatibility, and documentation. Return evidence-backed findings ordered by severity.
