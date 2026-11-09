# Backends

Select a built-in backend by constructing it and passing it to
`Sandbox(backend=...)`. Container, namespace and AppContainer backends share
the host or node kernel; the Windows Job Object backend is not a security
boundary. See [Backends](../backends/linux.md) for the guarantees, host
requirements and limitations of each one.

::: isolattice.LinuxNamespaceBackend

::: isolattice.WindowsJobBackend

::: isolattice.WindowsAppContainerBackend

::: isolattice.PodmanBackend

::: isolattice.DockerBackend

::: isolattice.KubernetesBackend
