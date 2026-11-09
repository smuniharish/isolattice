---
name: async-runtime
description: "Use for async execution, bounded streaming, cancellation, shutdown ownership or event-loop races."
---

# Async runtime

Read [API](../../../docs/architecture/03-public-api-specification.md),
[lifecycle](../../../docs/architecture/09-lifecycle-state-machine.md) and
[termination](../../../docs/architecture/11-termination-engine.md).

## Workflow

1. Confirm approved execution concurrency and stream-consumer contracts.
2. Use explicit operation ownership, deadlines and generation guards.
3. Drain both streams, enforce byte/queue budgets and expose gaps/truncation.
4. Route cancellation/timeout into owned termination before propagating.
5. Keep cleanup alive across repeated cancellation without unbounded shielding.
6. Test startup/stop races, slow consumers, closed stdin and failed cleanup.

Use asyncio/maintained clients. No blocking SDK call on the event loop, lock held
through indefinite I/O, unbounded communicate capture or silent dropped output.
ExceptionGroup cannot contain CancelledError; use appropriate base grouping.

Done requires bounded behavior tests, preserved errors and actual backend
cleanup evidence where claimed, with docs/examples.
