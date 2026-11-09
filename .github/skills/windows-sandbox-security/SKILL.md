---
name: windows-sandbox-security
description: "Use for Windows Job Objects, AppContainer, restricted tokens, ACLs, suspended launch or broker escape analysis."
---

# Windows sandbox security

Read [Windows design](../../../docs/backends/windows.md) and
[containment](../../../docs/architecture/10-process-containment.md).

## Workflow

1. Evaluate maintained bindings/native API coverage and approved security context.
2. Create suspended; configure tokens/ACLs/job and assign before resume.
3. Deny required breakaway and validate nested-host jobs/handle inheritance.
4. Restrict WMI/service/broker paths not covered by ordinary job inheritance.
5. Verify job identity/accounting and required release after termination.
6. Test brokered creation, detach/spawn, limits and parent death on approved hosts.

Job Objects are not per-process security restrictions or universal containment.
Do not use PID enumeration as final authority or resume before assignment.
Preserve native errors with redacted actionable diagnostics.

Done requires real Windows evidence, complete cleanup ownership, explicit
unsupported cases, tests, documentation and examples.
