# ═══════════════════════════════════════════════════════════════════════
# AgentUnderTest.psm1
#
# Thin module wrapper around Sentinel-Agent-Windows.ps1 for Pester testing
# (review finding M-03). The earlier test file dot-sourced the agent from
# inside a Load-Agent function, which scoped the imported names to that
# function; when Load-Agent returned, Test-TransportPolicy and Send-BeaconHTTP
# were no longer callable from the enclosing test scope.
#
# This module dot-sources the agent at module-import time, which imports the
# functions and $script:Config into the MODULE's scope. Pester tests use
# `Import-Module -Force` to re-import (picking up any new env vars set
# beforehand) and `InModuleScope` to reach $script:Config and to install
# Mocks that the module's own calls to Invoke-RestMethod will see.
# ═══════════════════════════════════════════════════════════════════════

$script:AgentPath = Join-Path $PSScriptRoot '..' 'Sentinel-Agent-Windows.ps1'

if (-not (Test-Path $script:AgentPath)) {
    throw "Sentinel-Agent-Windows.ps1 not found at $script:AgentPath"
}

# The agent's dot-source guard (`if ($MyInvocation.InvocationName -eq '.')`)
# skips its entry-point dispatch when dot-sourced, so this just loads the
# functions and initializes $script:Config from the current environment.
. $script:AgentPath

# Explicitly export the surface the Pester tests exercise. Everything else
# stays module-internal and reachable via InModuleScope when needed.
Export-ModuleMember -Function `
    Test-TransportPolicy, `
    Send-BeaconHTTP, `
    Get-EnvOrDefault, `
    Get-EnvBoolOrDefault, `
    Get-EnvIntOrDefault, `
    Write-SentinelLog
