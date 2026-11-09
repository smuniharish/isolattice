"""Opt-in real Kubernetes tests; a skipped test is not enforcement evidence."""

import asyncio
import dataclasses
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

from isolattice import (
    Capability,
    CapabilityState,
    ExecutionRequest,
    KubernetesBackend,
    NetworkAccess,
    Sandbox,
    SandboxCapabilityError,
    SandboxPolicy,
    SandboxTimeoutError,
    TerminationPolicy,
)
from isolattice.backends import kubernetes

pytestmark = pytest.mark.kubernetes

KubernetesConfig = tuple[str, str, str, str, str | None]


def fast_termination() -> TerminationPolicy:
    return TerminationPolicy(
        graceful_timeout=1,
        force_timeout=30,
        verification_interval=0.25,
        retry_delay=0.25,
        total_timeout=120,
    )


def backend_for(config: KubernetesConfig) -> KubernetesBackend:
    kubeconfig, context, namespace, image, executable = config
    return KubernetesBackend(
        kubeconfig=kubeconfig,
        context=context,
        namespace=namespace,
        image=image,
        executable=executable,
        command_timeout=30,
    )


def policy(**kwargs: Any) -> SandboxPolicy:
    return SandboxPolicy(
        network=NetworkAccess.ENABLED,
        termination=fast_termination(),
        **kwargs,
    )


async def owned_pods(config: KubernetesConfig) -> set[str]:
    kubeconfig, context, namespace, _, executable = config
    client = executable or shutil.which("kubectl")
    assert client is not None
    process = await asyncio.create_subprocess_exec(
        client,
        f"--kubeconfig={kubeconfig}",
        f"--context={context}",
        f"--namespace={namespace}",
        "get",
        "pods",
        f"--selector={kubernetes._OWNER_LABEL}",
        "--output=json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    async with asyncio.timeout(60):
        stdout, stderr = await process.communicate()
    assert process.returncode == 0, stderr
    return {item["metadata"]["uid"] for item in json.loads(stdout)["items"]}


def test_real_restricted_execution_io_and_cleanup(
    kubernetes_config: KubernetesConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ISOLATTICE_HOST_SECRET", f"host-only-{uuid.uuid4().hex}")
    script = (
        'printf "USER=%s:%s\\n" "$(id -u)" "$(id -g)"; '
        'IFS= read -r line; printf "INPUT=%s\\n" "$line"; '
        'printf "EXPLICIT=%s\\n" "$ISOLATTICE_EXPLICIT"; '
        'printf "HOST_SECRET=%s\\n" "${ISOLATTICE_HOST_SECRET-unset}"; '
        'printf "TOKEN=%s\\n" "$(ls /var/run/secrets 2>/dev/null || echo none)"; '
        "if touch /isolattice-readonly-probe 2>/dev/null; "
        "then echo WRITE_ALLOWED; else echo WRITE_DENIED; fi; "
        """awk '/^(CapEff|CapBnd|NoNewPrivs|Seccomp):/ { print $1 $2 }' /proc/self/status; """
        'printf "CWD=%s\\n" "$(pwd)"; '
        "printf warning >&2; exit 7"
    )

    async def check() -> None:
        before = await owned_pods(kubernetes_config)
        async with Sandbox(backend=backend_for(kubernetes_config)) as sandbox:
            (report,) = await sandbox.discover()
            assert report.available, report.error
            assert report.name == "kubernetes"
            assert (
                report.capabilities[Capability.NETWORK_ISOLATION].state
                is CapabilityState.UNSUPPORTED
            )
            async with await sandbox.create(policy=policy()) as instance:
                result = await instance.run(
                    ExecutionRequest(
                        ("/bin/sh", "-c", script),
                        cwd="/etc",
                        environment={"ISOLATTICE_EXPLICIT": "visible"},
                        stdin=b"from-host\n",
                    )
                )
        assert result.exit_code == 7
        assert result.stderr == b"warning"
        lines = result.stdout.decode().splitlines()
        assert lines == [
            "USER=65534:65534",
            "INPUT=from-host",
            "EXPLICIT=visible",
            "HOST_SECRET=unset",
            "TOKEN=none",
            "WRITE_DENIED",
            "CapEff:0000000000000000",
            "CapBnd:0000000000000000",
            "NoNewPrivs:1",
            "Seccomp:2",
            "CWD=/etc",
        ]
        assert await owned_pods(kubernetes_config) == before

    asyncio.run(check())


def test_real_memory_cpu_and_scratch_limits(kubernetes_config: KubernetesConfig) -> None:
    script = (
        "cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory.swap.max /sys/fs/cgroup/cpu.max; "
        "dd if=/dev/zero of=/tmp/fill bs=4096 count=4096 2>/dev/null; "
        'printf "DD=%s\\n" "$?"; printf "SIZE=%s\\n" "$(wc -c < /tmp/fill)"'
    )

    async def check() -> None:
        before = await owned_pods(kubernetes_config)
        limited = policy(
            memory_bytes=64 * 2**20,
            cpu_cores=0.25,
            scratch_bytes=8 * 2**20,
            required_capabilities=(Capability.MEMORY_LIMIT, Capability.CPU_LIMIT),
        )
        async with (
            Sandbox(backend=backend_for(kubernetes_config)) as sandbox,
            await sandbox.create(policy=limited) as instance,
        ):
            result = await instance.run(ExecutionRequest(("/bin/sh", "-c", script)))
        assert result.exit_code == 0, result.stderr
        assert result.stdout.decode().splitlines() == [
            str(64 * 2**20),
            "0",
            "25000 100000",
            "DD=1",
            f"SIZE={8 * 2**20}",
        ]
        assert await owned_pods(kubernetes_config) == before

    asyncio.run(check())


def test_real_timeout_deletes_the_pod(kubernetes_config: KubernetesConfig) -> None:
    async def check() -> None:
        before = await owned_pods(kubernetes_config)
        async with (
            Sandbox(backend=backend_for(kubernetes_config)) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            with pytest.raises(SandboxTimeoutError):
                await instance.run(ExecutionRequest(("/bin/sleep", "300"), timeout=3))
        assert await owned_pods(kubernetes_config) == before

    asyncio.run(check())


def test_real_prelude_rejection_fails_closed(
    kubernetes_config: KubernetesConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = kubernetes._Limits.checks

    def wrong_user(self: kubernetes._Limits, *, await_start: bool) -> object:
        return dataclasses.replace(original(self, await_start=await_start), uid=0)

    monkeypatch.setattr(kubernetes._Limits, "checks", wrong_user)

    async def check() -> None:
        before = await owned_pods(kubernetes_config)
        async with (
            Sandbox(backend=backend_for(kubernetes_config)) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            with pytest.raises(SandboxCapabilityError, match="prelude rejected"):
                await instance.run(ExecutionRequest(("/bin/echo", "never")))
        assert await owned_pods(kubernetes_config) == before

    asyncio.run(check())


def test_real_concurrent_executions_are_separate_pods(
    kubernetes_config: KubernetesConfig,
) -> None:
    async def check() -> None:
        before = await owned_pods(kubernetes_config)
        async with (
            Sandbox(backend=backend_for(kubernetes_config)) as sandbox,
            await sandbox.create(policy=policy()) as instance,
        ):
            results = await asyncio.gather(
                *(
                    instance.run(
                        ExecutionRequest(("/bin/sh", "-c", f"cat /etc/hostname; exit {code}"))
                    )
                    for code in (1, 2, 3)
                )
            )
        assert [result.exit_code for result in results] == [1, 2, 3]
        assert len({result.stdout for result in results}) == 3
        assert await owned_pods(kubernetes_config) == before

    asyncio.run(check())


EXAMPLE_CASES = [
    (
        "10-kubernetes",
        "kubernetes_isolated_run.py",
        (
            "Backend: kubernetes",
            "Kubernetes API v",
            "Unverifiable network isolation: rejected",
            "uid=65534",
            "caps=0000000000000000",
            "memory.max=67108864",
            "cpu.max=25000 100000",
            "root=read-only",
            "scratch=full",
            "token=none",
            "stdin=from the host",
            "greeting=hello",
            "Owned Pod released: True",
        ),
    ),
]


def test_every_kubernetes_example_has_a_live_test_case() -> None:
    root = Path(__file__).resolve().parents[2] / "examples"
    expected = {root / directory / filename for directory, filename, _ in EXAMPLE_CASES}
    assert set(root.rglob("kubernetes_*.py")) == expected


@pytest.mark.parametrize(("directory", "filename", "expected"), EXAMPLE_CASES)
def test_runnable_kubernetes_examples(
    kubernetes_config: KubernetesConfig, directory: str, filename: str, expected: tuple[str, ...]
) -> None:
    kubeconfig, context, namespace, image, executable = kubernetes_config
    root = Path(__file__).resolve().parents[2]
    interpreter = str(Path(sys.base_prefix) / "python.exe") if os.name == "nt" else sys.executable
    environment = dict(os.environ, PYTHONPATH=str(root / "src"))

    async def run() -> tuple[int, bytes, bytes]:
        before = await owned_pods(kubernetes_config)
        process = await asyncio.create_subprocess_exec(
            interpreter,
            str(root / "examples" / directory / filename),
            f"--kubeconfig={kubeconfig}",
            f"--context={context}",
            f"--namespace={namespace}",
            f"--image={image}",
            *((f"--executable={executable}",) if executable else ()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        async with asyncio.timeout(300):
            stdout, stderr = await process.communicate()
        assert process.returncode is not None
        assert await owned_pods(kubernetes_config) == before
        return process.returncode, stdout, stderr

    code, stdout, stderr = asyncio.run(run())
    assert code == 0, (stdout, stderr)
    assert stderr == b"", stderr
    for text in expected:
        assert text in stdout.decode()
