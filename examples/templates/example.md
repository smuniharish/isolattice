# Example category template

Use this structure for every `examples/NN-*/README.md` page. Describe only what exists:
link the real scripts and tests, and say plainly when a category has no scripts yet or
covers a proposed feature.

## Required metadata

`scripts/validate_repository.py` requires YAML front matter with exactly these values:

```yaml
---
category: 7
status: runnable
---
```

- `category` is an integer equal to the directory number (1 to 19).
- `status` is one of:
    - `runnable`: the directory has scripts, and the README names every one of them;
    - `index`: the directory has no scripts and points to scripts in other categories;
    - `proposed`: the feature is not implemented, the directory has no scripts, and
      the page says **Proposed.** in its opening paragraph.

## Required sections

The validator requires these level-two headings in every category page:
`## Prerequisites`, `## Acceptance`, `## Cleanup` and `## Security`. Runnable pages
also require `## Run` and `## Expected output`.

## Page outline

1. Front matter as above.
2. One H1, a sentence on what the category shows, and a table of scripts linked with
   absolute GitHub URLs
   (`https://github.com/smuniharish/isolattice/blob/main/examples/...`).
3. **Prerequisites**: Python 3.12+, `uv sync`, the backend, engine mode (rootless or
   rootful), images that must already be local and platform requirements.
4. **Run**: exact commands from the repository root, for example
   `uv run python examples/01-getting-started/podman_hello.py --connection podman-machine-default --image docker.io/library/alpine:3.22`.
5. **Expected output**: the lines the script prints, in a `text` block.
6. **Acceptance**: what the script checks before it succeeds, and the test file and
   environment variables that run it.
7. **Cleanup**: which owned resources are verified and removed, and when.
8. **Security**: the backend's threat model (shared-kernel backends exclude kernel,
   engine, runtime or cluster exploits; Windows Job Objects are not a security
   boundary), plus the backend's specific limitations.

## Rules for scripts

- Every script constructs a real backend, passes it to `Sandbox(backend=...)` and is
  run as a separate process by a live test. Script names start with `podman_`,
  `windows_`, `linux_`, `docker_` or `kubernetes_`.
- Scripts check measurable behavior and raise on any mismatch or unverified cleanup.
- Use only names exported by `isolattice`. Use an absolute `argv[0]` (`/bin/sh`).
- Never put credentials in example code or commands.

Link documentation with absolute URLs such as
`https://isolattice.readthedocs.io/en/latest/backends/podman/`.
