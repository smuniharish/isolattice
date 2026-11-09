# 19. Dependency evaluation

## Policy

The core package has no runtime dependencies. Platform enforcement uses operating
system primitives through the standard library (`asyncio`, `ctypes`, `tarfile`,
`hashlib`) and the trusted engine clients the user installs. A dependency is added
only when a maintained library is clearly safer than writing the code, after
evaluating maintenance, license, transitive dependencies, security history and
whether it can be optional.

## Runtime and optional dependencies

| Component | Used for | Decision |
| --- | --- | --- |
| Standard library | Runtime, models, termination, local store, events | Required; no third-party code |
| `ctypes` and system libraries | Linux `libc` and `libseccomp.so.2`; Windows `kernel32`, `kernelbase`, `advapi32` and `userenv` | Loaded only on the platform and by the backend that needs them |
| `podman`, `docker`, `kubectl` clients | Container backends | User-installed, invoked as argv vectors through a `CommandRunner`; never bundled |
| `aiobotocore>=3.10.0,<4` | `S3ArtifactStore`, extra `s3` | Accepted as optional |

### aiobotocore

- **Need:** an async S3 client with multipart uploads, streaming downloads and
  conditional writes for `S3ArtifactStore`.
- **Choice:** aiobotocore wraps botocore, the maintained AWS signing, endpoint and
  retry stack, and is natively asyncio. Alternatives were rejected: boto3 in worker
  threads would block threads per transfer, and a hand-written S3 client would
  re-implement request signing.
- **Cost:** botocore, aiohttp and their transitive dependencies. The locked
  versions are aiobotocore 3.10.0, botocore 1.43.117 and aiohttp 3.14.5.
- **Containment:** only the `s3` extra (and `all`) installs it, and it is imported
  only when an `S3ArtifactStore` performs an operation. The store creates a client
  per operation, writes with `If-None-Match: *`, keeps credentials out of `repr` and
  rejects client options that would override the endpoint, region or credentials.
- **License:** Apache-2.0, like Isolattice.

## Development tooling

Dependency groups in `pyproject.toml` (locked in `uv.lock`):

| Group | Tools |
| --- | --- |
| `test` | pytest 9.2.0, Hypothesis 6.170.15, coverage 7.17.2, pytest-cov 7.1.0, PyYAML 6.0.3, skills-ref 0.1.1 |
| `lint` | Ruff 0.17.4 for linting and formatting |
| `types` | Pyrefly 1.4.1 for static type checking, with `types-pyyaml` |
| `docs` | Zensical 0.1.0 and mkdocstrings-python 2.0.10 for the documentation site and API reference |

`dev` combines `test`, `lint` and `types` and is the default group. Markdown linting
uses `markdownlint-cli2` and diagrams are rendered with `@mermaid-js/mermaid-cli`;
both are Node.js tools installed globally, not project dependencies.

## Review triggers

Re-evaluate a dependency when its major version changes, a security advisory affects
it, its maintenance stops or a smaller maintained alternative appears. New runtime
dependencies need an architecture decision; see the
[architecture decisions](22-architecture-decision-records.md).
