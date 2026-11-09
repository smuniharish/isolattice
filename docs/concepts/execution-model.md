# Execution model

## Ownership

```text
Sandbox                  owns the backend, artifact store and event sink
└── SandboxInstance      one policy on one backend session
    └── ExecutionHandle  one execution in its own boundary
```

Each owner closes what it owns. Leaving `async with Sandbox(...)` closes every
instance, and closing an instance terminates every active execution. Every step
is verified.

## One boundary per execution

Every `run` or `start` call gets its own boundary: a container, a Pod, a
namespace set or a Job Object. Executions in one instance therefore:

- run concurrently, up to `SandboxPolicy.max_concurrent_executions` (default 4).
  Starting one more raises `SandboxStateError`;
- are cancelled independently, so cancelling one leaves its siblings running;
- share files only through a persistent workspace (`workspace=True`), mounted
  at `SandboxInstance.workspace_path`.

## Running a command

| Call | Returns | Use when |
| --- | --- | --- |
| `await instance.run(request)` | `ExecutionResult` | You want the whole result. |
| `await instance.start(request)` | `ExecutionHandle` | You want to stream output, cancel, checkpoint or snapshot. |

`ExecutionRequest` fields:

- `argv`: a tuple of strings. Use an absolute path for `argv[0]`.
- `stdin`: optional bytes.
- `environment`: the only variables the workload sees on Podman and the native
  backends.
- `cwd`: the working directory inside the boundary.
- `timeout`: overrides `SandboxPolicy.execution_timeout` for this request.

## Output

Output is delivered in bounded `OutputChunk`s through a queue of
`output_queue_chunks` entries. A full queue applies backpressure to the
workload. `ExecutionResult.stdout` and `stderr` keep at most `stdout_limit` and
`stderr_limit` bytes. `stdout_truncated` and `stderr_truncated` say whether
anything was dropped.

## Ending an execution

| Outcome | Result |
| --- | --- |
| The process exits | `exit_code` is set. Termination still verifies that the boundary is empty. |
| The timeout expires | `SandboxTimeoutError` after verified termination. |
| `handle.cancel()` or task cancellation | Verified termination, then the cancellation continues. |
| Termination cannot be verified | `TerminationVerificationError`. The state is `unknown`; cleanup stays owned and is retried. |

`ExecutionResult.termination` is a `TerminationReport` with the number of
attempts and observations, the final `state` (`terminated`, `non_empty` or
`unknown`) and whether the backend released its resources.

See the [lifecycle](../architecture/09-lifecycle-state-machine.md) and
[termination engine](../architecture/11-termination-engine.md) for the full design.
