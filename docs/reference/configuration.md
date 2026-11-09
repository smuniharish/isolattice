# Configuration reference

Isolattice has no configuration file and reads no environment variables at run time.
Everything is passed explicitly to constructors. This page lists every constructor
argument; per-sandbox behaviour lives in the [policy reference](policies.md).

## Sandbox

`Sandbox(*, backend, artifact_store=None, event_sink=None, staging_directory=None)`;
see the [Sandbox API](../api/sandbox.md).

| Argument | Default | Meaning |
| --- | --- | --- |
| `backend` | required | The `Backend` that prepares every instance |
| `artifact_store` | `None` | `ArtifactStore` for snapshots and checkpoints; artifact operations raise `SandboxCapabilityError` without one |
| `event_sink` | `LoggingEventSink()` | Receives lifecycle [events](events.md) |
| `staging_directory` | system temporary directory | Parent of private per-operation staging directories; staged checkpoints hold workload memory |

## Backends

Select a built-in backend by constructing it and passing it to
`Sandbox(backend=...)`. Container, namespace and AppContainer backends share the
host or node kernel; the Windows Job Object backend is not a security boundary.
See the [backends API](../api/backends.md).

### PodmanBackend

| Argument | Default | Meaning |
| --- | --- | --- |
| `connection` | required | Named Podman connection passed to every command |
| `image` | required | Local image; resolved to its immutable ID and never pulled |
| `executable` | `None` | Path of the `podman` client; `None` searches `PATH` |
| `command_timeout` | `15.0` | Seconds per engine command |
| `allow_rootful` | `False` | Permit a rootful engine (needed for instance budgets and checkpoints) |
| `transfer_timeout` | `300.0` | Seconds for workspace, snapshot and checkpoint transfers |
| `runner` | `None` | [`CommandRunner`](../guides/custom-integrations.md#custom-command-runner); `None` uses `SubprocessRunner` |
| `helper_interpreter` | `None` | Python that filters restored checkpoint logs; `None` uses the running interpreter |

### DockerBackend

| Argument | Default | Meaning |
| --- | --- | --- |
| `host` | required | Docker API endpoint passed as `--host`, for example `unix:///run/user/1000/docker.sock`; `DOCKER_HOST` and CLI contexts are ignored |
| `image` | required | Local image; never pulled |
| `executable` | `None` | Path of the `docker` client; `None` searches `PATH` |
| `command_timeout` | `15.0` | Seconds per engine command |
| `allow_rootful` | `False` | Permit a rootful engine |
| `runner` | `None` | `CommandRunner`; `None` uses `SubprocessRunner` |

### KubernetesBackend

| Argument | Default | Meaning |
| --- | --- | --- |
| `kubeconfig` | required | Kubeconfig file passed to every `kubectl` call |
| `context` | required | Context name; the current context is never used |
| `namespace` | required | Namespace for Pods; must be `Active` |
| `image` | required | Image present on the nodes (`imagePullPolicy: Never`) |
| `executable` | `None` | Path of `kubectl`; `None` searches `PATH` |
| `command_timeout` | `15.0` | Seconds per `kubectl` call |
| `startup_timeout` | `60.0` | Seconds a Pod may take to start running |
| `runner` | `None` | `CommandRunner`; `None` uses `SubprocessRunner` |

### LinuxNamespaceBackend

| Argument | Default | Meaning |
| --- | --- | --- |
| `cgroup_root` | required | Absolute path of a delegated cgroup v2 directory you own |
| `read_only_paths` | `/usr`, `/bin`, `/sbin`, `/lib`, `/lib32`, `/lib64`, `/libx32`, `/etc` | Host paths bound read-only into the sandbox; absolute, not `/` |
| `state_directory` | `None` | Absolute parent of per-instance state directories |
| `startup_timeout` | `15.0` | Seconds for the namespace helper to start |
| `helper_interpreter` | `None` | Absolute Python path for the helper; `None` uses the running interpreter |

### WindowsJobBackend

| Argument | Default | Meaning |
| --- | --- | --- |
| `working_directory` | required | Existing absolute directory that workloads run under; `ExecutionRequest.cwd` maps below it |
| `restricted_token` | `False` | Launch under a restricted, low-integrity token; see [Windows](../backends/windows.md#restricted-low-integrity-tokens) |
| `startup_timeout` | `30.0` | Seconds for the restricted launcher to start the workload |
| `helper_interpreter` | `None` | Python for the launcher; `None` uses the base interpreter |

### WindowsAppContainerBackend

| Argument | Default | Meaning |
| --- | --- | --- |
| `read_only_paths` | `()` | Host paths the AppContainer may read |
| `state_directory` | `None` | Parent of per-instance directories; `None` uses the temporary directory |
| `less_privileged` | `True` | Use a less-privileged AppContainer (LPAC); `False` uses a plain AppContainer |
| `startup_timeout` | `15.0` | Seconds for the launcher to start the workload |
| `helper_interpreter` | `None` | Python for the launcher; `None` uses the base interpreter |

## Artifact stores and event sinks

See the [storage API](../api/storage.md) and [events API](../api/events.md).

| Constructor | Arguments |
| --- | --- |
| `LocalArtifactStore(root)` | `root`: existing directory |
| `S3ArtifactStore(*, bucket, prefix="", endpoint_url=None, region_name=None, credentials=None, part_size=8 MiB, client_options=None)` | Requires `isolattice[s3]`; see [S3 client options](../guides/custom-integrations.md#s3-client-options) |
| `S3Credentials(access_key_id, secret_access_key, session_token=None)` | Secret and token are hidden from `repr` |
| `LoggingEventSink(logger=None)` | `None` uses the `isolattice` logger |

## Fixed limits

| Limit | Value |
| --- | --- |
| Output chunk size | 64 KiB |
| Environment size per request | 64 KiB |
| Artifact size | 16 GiB |
| Archive members | 1,000,000 |
| Manifest size | 64 KiB |
| Artifact key length | 512 characters |

Artifact keys are `/`-separated segments of `A-Z a-z 0-9 . _ -` that do not start with
`.` or `-`, do not end with `.`, are not Windows device names and do not end in
`.manifest.json`. `validate_artifact_key(key)` checks a key.

## Test environment variables

The integration tests, not the library, read these variables to find real engines.
See [testing](../development/testing.md).

| Variables | Used by |
| --- | --- |
| `ISOLATTICE_TEST_PODMAN_CONNECTION`, `ISOLATTICE_TEST_PODMAN_IMAGE` | Rootless Podman tests |
| `ISOLATTICE_TEST_PODMAN_ROOTFUL_*` | Rootful Podman tests (instance budgets, checkpoints) |
| `ISOLATTICE_TEST_PODMAN_RESOURCE_*` | Podman resource-limit tests |
| `ISOLATTICE_TEST_DOCKER_HOST`, `_IMAGE`, `_EXECUTABLE`, `_ROOTFUL` | Docker API tests |
| `ISOLATTICE_TEST_KUBERNETES_KUBECONFIG`, `_CONTEXT`, `_NAMESPACE`, `_IMAGE`, `_EXECUTABLE` | Kubernetes tests |
| `ISOLATTICE_TEST_LINUX_CGROUP_ROOT` | Linux namespace tests |
