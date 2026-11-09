---
name: termination-engine
description: "Use for graceful/force termination, retries, stability verification, stop/kill, timeout or cleanup failure."
---

# Termination engine

Read [termination](../../../docs/architecture/11-termination-engine.md) and
[errors](../../../docs/architecture/12-exception-model.md).

## Workflow

1. Join one owned coordinator per generation and freeze admissions.
2. Apply bounded grace/force/observation phases under a total deadline.
3. Count attempts as total attempts; default three means three, not four.
4. Retry NON_EMPTY; treat UNKNOWN as failure unless recovered.
5. Require bounded stable EMPTY and required resource release for TERMINATED.
6. Preserve cancellation/execute/cleanup causes and safe per-attempt diagnostics.
7. Test every trigger, respawn, uncertainty, stability regression and release failure.

Use backend observation authority; no direct-child/PID-tree success proxy.
Repeated cancelled waiters must not abandon cleanup.

Done requires exact retry/deadline/state tests and real backend verification
where enforcement is claimed, plus docs/examples.
