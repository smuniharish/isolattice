# Performance

Isolattice adds little work of its own. Most of the cost is the boundary: creating and
removing a container, a Pod, a namespace set or a Job Object for every
execution. No benchmark results are published yet. Measure on your own hosts
with the policy you will deploy.

## What costs time

| Step | Cost | Lever |
| --- | --- | --- |
| Discovery | One probe per backend when an instance is created | Reuse one `Sandbox` for many instances. |
| Boundary creation | Container create and start, or Pod scheduling | Use local images, and use Linux namespaces or Windows where they fit. |
| Resource bootstrap | Verifying kernel limits before the workload starts | Only set limits you need. |
| Termination | Graceful stop, forced stop and re-observation | Tune `TerminationPolicy`, but never skip verification. |
| Snapshots and checkpoints | Archiving, hashing and transfer | Keep workspaces small; use a nearby store. |

## Throughput

- Executions in one instance run concurrently, up to
  `max_concurrent_executions`. Raise it for parallel workloads within the
  engine's capacity.
- Run independent instances for independent tenants or policies.
- Read output promptly. A full `output_queue_chunks` queue pauses the workload.

## Measuring fairly

When comparing with another tool, use the same guarantees on both sides. A bare
`subprocess` call is faster because it creates no boundary and verifies nothing.
Record the host, kernel, engine version, policy and the P50, P95 and P99
latencies, including termination.

Optimizations must not weaken a required guarantee or replace verification of
the boundary's identity with PID polling.
