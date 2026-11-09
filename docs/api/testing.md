# Conformance testing

`isolattice.testing` checks that your own extension implementations keep their
documented contracts. The checks are plain coroutines with no test-framework
dependency, so they run under pytest, unittest or a deployment health check.

```python
from isolattice.testing import check_artifact_store


async def test_my_store(tmp_path):
    await check_artifact_store(MyDatabaseStore(), tmp_path)
```

::: isolattice.testing.check_artifact_store

::: isolattice.testing.ConformanceError
