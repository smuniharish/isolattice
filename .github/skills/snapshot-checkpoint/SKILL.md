---
name: snapshot-checkpoint
description: "Use for cold snapshots, artifact storage, safe archives, integrity, CRIU checkpoints or restore compatibility."
---

# Snapshots and checkpoints

Read [snapshots](../../../docs/architecture/13-snapshot-architecture.md) and
[checkpoints](../../../docs/architecture/14-checkpoint-architecture.md).

## Workflow

1. Distinguish cold filesystem state from live process state.
2. Define consistency, owner authorization, manifest/version and quotas.
3. Use explicit restrictive extraction with race-safe owned staging.
4. Separate integrity/authenticity/confidentiality and exclude config secrets.
5. Treat memory checkpoints as sensitive; qualify kernel/runtime/resources.
6. Restore to a new generation and revalidate current policy.
7. Test tampering, bombs, links, partial writes and incompatible restore.

Use reviewed archive/storage libraries and genuine runtime/CRIU interfaces.
No filesystem copy called a checkpoint, implicit tar defaults or arbitrary host
path publication. Preserve partial-failure diagnostics and cleanup errors.

Done requires real snapshot/restore or checkpoint/restore evidence, typed
storage contracts, docs/examples and explicit unsupported capabilities.
