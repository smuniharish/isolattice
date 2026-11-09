#Requires -Version 5.1
<#
.SYNOPSIS
Installs the delegated rootless Podman API service into a WSL Podman machine
and registers a host connection for it.
.EXAMPLE
./Install-IsolatticeRootless.ps1 -Machine podman-machine-default
#>
param(
    [string]$Machine = 'podman-machine-default',
    [int]$Uid = 1000,
    [string]$Connection = "$Machine-isolattice"
)
$ErrorActionPreference = 'Stop'
function Get-LfBase64([string]$Path) {
    $text = [IO.File]::ReadAllText((Join-Path $PSScriptRoot $Path)) -replace "`r", ''
    [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($text))
}
$script = Get-LfBase64 'isolattice-rootless-podman.sh'
$unit = Get-LfBase64 'isolattice-rootless-podman@.service'
$installer = Get-LfBase64 'install.sh'
$remote = "echo $installer | base64 -d > /tmp/isolattice-install.sh && " +
    "ISOLATTICE_UID=$Uid ISOLATTICE_SCRIPT=$script ISOLATTICE_UNIT=$unit sh /tmp/isolattice-install.sh"
podman machine ssh $Machine "sudo sh -c '$remote'"
if ($LASTEXITCODE -ne 0) { throw "installation failed in machine $Machine" }

$connections = podman system connection list --format json | Out-String | ConvertFrom-Json
$existing = $connections | ForEach-Object { $_ } | Where-Object { $_.Name -eq $Machine } |
    Select-Object -First 1
if ($null -eq $existing) { throw "connection $Machine was not found" }
$uri = [Uri]$existing.URI
$target = "ssh://{0}@{1}:{2}/run/user/{3}/podman/isolattice.sock" -f `
    $uri.UserInfo, $uri.Host, $uri.Port, $Uid
podman system connection remove $Connection 2>$null | Out-Null
podman system connection add $Connection --identity $existing.Identity $target
if ($LASTEXITCODE -ne 0) { throw "could not add connection $Connection" }
podman --connection $Connection info --format "rootless={{.Host.Security.Rootless}} controllers={{.Host.CgroupControllers}}"
