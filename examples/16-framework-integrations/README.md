---
category: 16
status: proposed
---

# 16 Framework integrations

**Proposed.** Framework integrations are not implemented and this directory has no
scripts. Isolattice provides no FastAPI, Starlette, Jupyter or worker-queue adapters.

Because the core API is async, an application can already own a `Sandbox` in its own
lifespan handler and call `create()` and `run()` from request handlers, but no example
or tested pattern for that exists here yet.

## Prerequisites

Planned examples need pinned framework versions and, for servers, authenticated and
authorized owner-scoped access with bounded request and response bodies.

## Acceptance

Planned coverage: FastAPI/Starlette lifespan ownership, plain asyncio applications,
Jupyter, data and ML jobs, LLM-generated code and background workers, including
request cancellation, concurrency budgets, streamed responses and shutdown.

## Cleanup

The application lifespan must own sandbox instances and close them within a bounded
shutdown. A client disconnect must not orphan accepted work.

## Security

- Code produced by an LLM is untrusted. Built-in backends share the host (or node)
  kernel, so kernel, engine, runtime or cluster exploits are out of scope, and the
  Windows Job Object backend is not a security boundary.
- Never expose an unauthenticated code-execution endpoint or pass host credentials to
  workloads.

See the [public API specification](https://isolattice.readthedocs.io/en/latest/architecture/03-public-api-specification/).
