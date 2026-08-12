# ═══════════════════════════════════════════════════════════════════════
# Pester 5 tests for Sentinel-Agent-Windows.ps1
#
# Loader strategy (review finding M-03):
#   The agent is dot-sourced by AgentUnderTest.psm1 at module import. Tests
#   re-import the module with -Force after mutating HBV_* env vars so a
#   fresh $script:Config is built from the intended environment. InModuleScope
#   is used to reach $script:Config and to install Mocks that the module's
#   own Invoke-RestMethod calls will actually see.
#
# What these tests prove (all executable in a real PowerShell 7 runtime):
#   - Default HBV_COLLECTOR_URL is https://
#   - AllowInsecure defaults to $false
#   - Test-TransportPolicy refuses http:// unless HBV_ALLOW_INSECURE=true
#   - Send-BeaconHTTP short-circuits BEFORE Invoke-RestMethod when refused
#   - HBV_ALLOW_INSECURE=true lets http:// through (lab override works)
#   - HBV_TLS_CA_BUNDLE=false forces -SkipCertificateCheck
#   - Default TLS verification is ON (no -SkipCertificateCheck)
# ═══════════════════════════════════════════════════════════════════════

BeforeAll {
    $script:ModulePath = Join-Path $PSScriptRoot 'AgentUnderTest.psm1'

    function Reset-HbvEnv {
        <#
          Clear every HBV_* env var so a fresh Import-Module gets a clean
          starting environment. Callers set what they need immediately after.
        #>
        Get-ChildItem Env: |
            Where-Object { $_.Name -like 'HBV_*' } |
            ForEach-Object { Remove-Item "Env:$($_.Name)" -ErrorAction SilentlyContinue }
    }

    function Import-Agent {
        param([hashtable]$Env = @{})
        Reset-HbvEnv
        foreach ($k in $Env.Keys) { Set-Item "Env:$k" $Env[$k] }
        # -Force re-imports the module fresh, which re-runs the dot-source
        # and rebuilds $script:Config from the just-set environment.
        Import-Module $script:ModulePath -Force -DisableNameChecking
    }
}

Describe 'Sentinel Windows Agent — configuration defaults' {

    It 'defaults HBV_COLLECTOR_URL to https://' {
        Import-Agent
        InModuleScope AgentUnderTest {
            $script:Config.APIEndpoint | Should -Match '^https://'
        }
    }

    It 'defaults AllowInsecure to $false' {
        Import-Agent
        InModuleScope AgentUnderTest {
            $script:Config.AllowInsecure | Should -BeFalse
        }
    }

    It 'leaves TLSCaBundle unset by default' {
        Import-Agent
        InModuleScope AgentUnderTest {
            $script:Config.TLSCaBundle | Should -BeNullOrEmpty
        }
    }
}

Describe 'Sentinel Windows Agent — Test-TransportPolicy' {

    It 'refuses http:// without HBV_ALLOW_INSECURE' {
        Import-Agent -Env @{ HBV_COLLECTOR_URL = 'http://collector.local:8443/api/beacon' }
        InModuleScope AgentUnderTest {
            Test-TransportPolicy -Endpoint $script:Config.APIEndpoint | Should -BeFalse
        }
    }

    It 'allows http:// with HBV_ALLOW_INSECURE=true' {
        Import-Agent -Env @{
            HBV_COLLECTOR_URL  = 'http://collector.local:8443/api/beacon'
            HBV_ALLOW_INSECURE = 'true'
        }
        InModuleScope AgentUnderTest {
            Test-TransportPolicy -Endpoint $script:Config.APIEndpoint | Should -BeTrue
        }
    }

    It 'allows https:// out of the box' {
        Import-Agent -Env @{ HBV_COLLECTOR_URL = 'https://collector.local/api/beacon' }
        InModuleScope AgentUnderTest {
            Test-TransportPolicy -Endpoint $script:Config.APIEndpoint | Should -BeTrue
        }
    }

    It 'refuses non-http schemes' {
        Import-Agent -Env @{ HBV_COLLECTOR_URL = 'ftp://collector.local/api/beacon' }
        InModuleScope AgentUnderTest {
            Test-TransportPolicy -Endpoint $script:Config.APIEndpoint | Should -BeFalse
        }
    }
}

Describe 'Sentinel Windows Agent — Send-BeaconHTTP behaviour' {

    It 'does NOT call Invoke-RestMethod when transport is refused' {
        Import-Agent -Env @{ HBV_COLLECTOR_URL = 'http://collector.local:8443/api/beacon' }

        InModuleScope AgentUnderTest {
            # Mock inside the module scope so the module's own
            # Invoke-RestMethod call resolves to the mock.
            Mock Invoke-RestMethod {
                throw 'Invoke-RestMethod must not be called for a refused endpoint'
            }

            $result = Send-BeaconHTTP -Metrics @{ agent_id = 'x'; timestamp = 0 }
            $result | Should -BeFalse
            Should -Invoke Invoke-RestMethod -Times 0 -Exactly
        }
    }

    It 'calls Invoke-RestMethod for https:// and omits -SkipCertificateCheck by default' {
        Import-Agent -Env @{
            HBV_COLLECTOR_URL = 'https://collector.local/api/beacon'
            HBV_API_KEY       = 'unit-test-key'
        }

        InModuleScope AgentUnderTest {
            $script:Captured = $null
            Mock Invoke-RestMethod {
                # $PSBoundParameters reflects what the caller actually passed —
                # including switch parameters like -SkipCertificateCheck.
                $script:Captured = @{
                    Uri                  = $PSBoundParameters['Uri']
                    SkipCertificateCheck = $PSBoundParameters.ContainsKey('SkipCertificateCheck')
                    HasApiKey            = $PSBoundParameters['Headers'].ContainsKey('X-API-Key')
                }
                return @{ status = 'success' }
            }

            $result = Send-BeaconHTTP -Metrics @{ agent_id = 'y'; timestamp = 1 }
            $result | Should -BeTrue
            $script:Captured.Uri                  | Should -BeExactly 'https://collector.local/api/beacon'
            $script:Captured.SkipCertificateCheck | Should -BeFalse
            $script:Captured.HasApiKey            | Should -BeTrue
        }
    }

    It 'passes -SkipCertificateCheck when HBV_TLS_CA_BUNDLE=false' {
        Import-Agent -Env @{
            HBV_COLLECTOR_URL = 'https://collector.local/api/beacon'
            HBV_API_KEY       = 'unit-test-key'
            HBV_TLS_CA_BUNDLE = 'false'
        }

        InModuleScope AgentUnderTest {
            $script:Captured = $null
            Mock Invoke-RestMethod {
                $script:Captured = @{
                    SkipCertificateCheck = $PSBoundParameters.ContainsKey('SkipCertificateCheck')
                }
                return @{ status = 'success' }
            }

            $null = Send-BeaconHTTP -Metrics @{ agent_id = 'z'; timestamp = 2 }
            $script:Captured.SkipCertificateCheck | Should -BeTrue
        }
    }

    It 'lab-only cleartext override actually reaches Invoke-RestMethod' {
        Import-Agent -Env @{
            HBV_COLLECTOR_URL  = 'http://collector.local:8443/api/beacon'
            HBV_ALLOW_INSECURE = 'true'
            HBV_API_KEY        = 'unit-test-key'
        }

        InModuleScope AgentUnderTest {
            $script:Captured = $null
            Mock Invoke-RestMethod {
                $script:Captured = @{ Uri = $PSBoundParameters['Uri'] }
                return @{ status = 'success' }
            }

            $result = Send-BeaconHTTP -Metrics @{ agent_id = 'lab'; timestamp = 3 }
            $result | Should -BeTrue
            $script:Captured.Uri | Should -Match '^http://'
        }
    }
}
