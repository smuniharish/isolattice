---
name: capability-model
description: "Use for capability discovery, evidence freshness, required/preferred policies or backend matching."
---

# Capability model

Read [capabilities](../../../docs/architecture/06-capability-model.md) and
[policy](../../../docs/architecture/05-policy-model.md).

## Workflow

1. Define exact granular guarantees, not just broad feature names.
2. Probe dependencies, privileges, kernel/runtime features and configuration.
3. Scope evidence to identity/fingerprint/time; invalidate failed/stale probes.
4. Reject unmet REQUIRED guarantees; record approved PREFERRED alternatives.
5. Revalidate actual setup before READY.
6. Test LIMITED matching, stale evidence and ambiguous network/resource semantics.

Use maintained probes and explicit trust for plugins/remote reports.
No OS-name inference, automatic LIMITED equivalence or self-certified security.
DISABLED optional facilities must not ambiguously mean unrestricted access.

Done requires typed evidence, honest limitations, matching tests, API/docs/
examples coverage and real enforcement qualification when advertised.
