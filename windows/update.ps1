param(
    [switch]$NoLaunch
)

$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
& (Join-Path $Root 'install.ps1') -NoLaunch:$NoLaunch
