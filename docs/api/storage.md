# Artifact storage

Snapshots and checkpoints are published to an `ArtifactStore`. Isolattice
computes every manifest itself and re-verifies size and SHA-256 after each
fetch, so a store keeps bytes but never vouches for integrity. Subclass
`ArtifactStore` to keep artifacts anywhere, then check it with
[`check_artifact_store`](testing.md).

```python
from isolattice import LocalArtifactStore, S3ArtifactStore, Sandbox

local = Sandbox(backend=backend, artifact_store=LocalArtifactStore("artifacts"))
remote = Sandbox(backend=backend, artifact_store=S3ArtifactStore(bucket="sandbox-artifacts"))
```

`S3ArtifactStore` needs the `s3` extra: `pip install "isolattice[s3]"`.

::: isolattice.ArtifactStore

::: isolattice.LocalArtifactStore

::: isolattice.S3ArtifactStore

::: isolattice.S3Credentials
