<#
.SYNOPSIS
    HoneyBadger Sentinel - Windows Agent

.DESCRIPTION
    Lightweight monitoring agent that beacons system metrics to central collector.
    C2-style architecture with MQTT and HTTP fallback.

.NOTES
    Author: HoneyBadger
    Version: 1.1.0
    CyberShield 2026 - Infrastructure Monitoring
#>

#Requires -Version 7.0

# ═══════════════════════════════════════════════════════════════════════
# CONFIGURATION (with environment variable support)
# ═══════════════════════════════════════════════════════════════════════

function Get-EnvOrDefault {
    param(
        [string]$Name,
        [string]$Default
    )
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ($value) { return $value }
    return $Default
}

function Get-EnvIntOrDefault {
    param(
        [string]$Name,
        [int]$Default
    )
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ($value) {
        try { return [int]$value } catch { return $Default }
    }
    return $Default
}

function Get-EnvBoolOrDefault {
    param(
        [string]$Name,
        [bool]$Default
    )
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ($value) {
        return $value -in @('true', '1', 'yes')
    }
    return $Default
}

$script:Config = @{
    # Agent Identity
    AgentID = Get-EnvOrDefault -Name "HBV_AGENT_ID" -Default $env:COMPUTERNAME
    AgentType = "windows"

    # Collector Configuration
    APIEndpoint = Get-EnvOrDefault -Name "HBV_COLLECTOR_URL" -Default "http://192.168.36.241:8443/api/beacon"
    APIKey = Get-EnvOrDefault -Name "HBV_API_KEY" -Default ""

    # Beacon Settings
    BeaconInterval = Get-EnvIntOrDefault -Name "HBV_BEACON_INTERVAL" -Default 30
    MaxRetries = Get-EnvIntOrDefault -Name "HBV_MAX_RETRIES" -Default 3
    RetryDelay = Get-EnvIntOrDefault -Name "HBV_RETRY_DELAY" -Default 5
    RequestTimeout = Get-EnvIntOrDefault -Name "HBV_REQUEST_TIMEOUT" -Default 10

    # Queue Settings (offline resilience)
    QueuePath = Get-EnvOrDefault -Name "HBV_QUEUE_PATH" -Default "$env:TEMP\HBV-Sentinel-Queue"
    MaxQueueSize = Get-EnvIntOrDefault -Name "HBV_MAX_QUEUE_SIZE" -Default 100

    # Metrics Collection
    CollectCPU = Get-EnvBoolOrDefault -Name "HBV_COLLECT_CPU" -Default $true
    CollectMemory = Get-EnvBoolOrDefault -Name "HBV_COLLECT_MEMORY" -Default $true
    CollectDisk = Get-EnvBoolOrDefault -Name "HBV_COLLECT_DISK" -Default $true
    CollectNetwork = Get-EnvBoolOrDefault -Name "HBV_COLLECT_NETWORK" -Default $true
    CollectServices = Get-EnvBoolOrDefault -Name "HBV_COLLECT_SERVICES" -Default $true
    CollectGPU = Get-EnvBoolOrDefault -Name "HBV_COLLECT_GPU" -Default $true

    # Logging
    LogPath = Get-EnvOrDefault -Name "HBV_LOG_PATH" -Default "$env:TEMP\HBV-Sentinel.log"
    LogLevel = Get-EnvOrDefault -Name "HBV_LOG_LEVEL" -Default "INFO"
}

# Graceful shutdown flag
$script:ShutdownRequested = $false

# ═══════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════

function Write-SentinelLog {
    param(
        [string]$Message,
        [ValidateSet("DEBUG", "INFO", "WARN", "ERROR")]
        [string]$Level = "INFO"
    )
    
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logEntry = "[$timestamp] [$Level] $Message"
    
    # Console output with color
    $color = switch ($Level) {
        "DEBUG" { "Gray" }
        "INFO"  { "Cyan" }
        "WARN"  { "Yellow" }
        "ERROR" { "Red" }
    }
    
    Write-Host $logEntry -ForegroundColor $color
    
    # File output
    try {
        Add-Content -Path $script:Config.LogPath -Value $logEntry -ErrorAction SilentlyContinue
    } catch {
        # Silent fail if can't write log
    }
}

# ═══════════════════════════════════════════════════════════════════════
# METRICS COLLECTION
# ═══════════════════════════════════════════════════════════════════════

function Get-SystemMetrics {
    $metrics = @{
        timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        agent_id = $script:Config.AgentID
        agent_type = $script:Config.AgentType
    }
    
    try {
        # CPU Usage
        if ($script:Config.CollectCPU) {
            $cpu = (Get-Counter '\Processor(_Total)\% Processor Time' -ErrorAction Stop).CounterSamples.CookedValue
            $metrics['cpu_percent'] = [math]::Round($cpu, 2)
        }
        
        # Memory Usage
        if ($script:Config.CollectMemory) {
            $os = Get-CimInstance Win32_OperatingSystem
            $totalRAM = $os.TotalVisibleMemorySize
            $freeRAM = $os.FreePhysicalMemory
            $usedRAM = $totalRAM - $freeRAM
            $memPercent = [math]::Round(($usedRAM / $totalRAM) * 100, 2)
            
            $metrics['memory_total_mb'] = [math]::Round($totalRAM / 1024, 2)
            $metrics['memory_used_mb'] = [math]::Round($usedRAM / 1024, 2)
            $metrics['memory_percent'] = $memPercent
        }
        
        # Disk Usage (C: drive)
        if ($script:Config.CollectDisk) {
            $disk = Get-PSDrive -Name C
            $diskTotal = ($disk.Used + $disk.Free) / 1GB
            $diskUsed = $disk.Used / 1GB
            $diskPercent = [math]::Round(($diskUsed / $diskTotal) * 100, 2)
            
            $metrics['disk_total_gb'] = [math]::Round($diskTotal, 2)
            $metrics['disk_used_gb'] = [math]::Round($diskUsed, 2)
            $metrics['disk_percent'] = $diskPercent
        }
        
        # Network Stats
        if ($script:Config.CollectNetwork) {
            $adapters = Get-NetAdapter | Where-Object { $_.Status -eq "Up" }
            $metrics['network_adapters'] = $adapters.Count
            $metrics['network_speed_gbps'] = ($adapters | Measure-Object -Property LinkSpeed -Sum).Sum / 1000000000
        }
        
        # GPU Stats (if NVIDIA present)
        if ($script:Config.CollectGPU) {
            $nvidiaSmi = "C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"
            if (Test-Path $nvidiaSmi) {
                try {
                    $gpuInfo = & $nvidiaSmi --query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits
                    $parts = $gpuInfo -split ','
                    
                    $metrics['gpu_util_percent'] = [int]$parts[0].Trim()
                    $metrics['gpu_mem_util_percent'] = [int]$parts[1].Trim()
                    $metrics['gpu_mem_used_mb'] = [int]$parts[2].Trim()
                    $metrics['gpu_mem_total_mb'] = [int]$parts[3].Trim()
                    $metrics['gpu_temp_c'] = [int]$parts[4].Trim()
                } catch {
                    Write-SentinelLog "Failed to collect GPU metrics: $($_.Exception.Message)" -Level "WARN"
                }
            }
        }
        
        # Critical Services (HBV-specific)
        if ($script:Config.CollectServices) {
            $criticalServices = @("Ollama", "Docker")
            $serviceStatus = @{}
            
            foreach ($svc in $criticalServices) {
                $service = Get-Service -Name $svc -ErrorAction SilentlyContinue
                if ($service) {
                    $serviceStatus[$svc] = $service.Status.ToString()
                }
            }
            
            if ($serviceStatus.Count -gt 0) {
                $metrics['services'] = $serviceStatus
            }
        }
        
        # System Uptime
        $uptime = (Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
        $metrics['uptime_seconds'] = [int]$uptime.TotalSeconds
        
        Write-SentinelLog "Collected metrics: CPU $($metrics['cpu_percent'])%, Memory $($metrics['memory_percent'])%, Disk $($metrics['disk_percent'])%" -Level "DEBUG"
        
        return $metrics
        
    } catch {
        Write-SentinelLog "Error collecting metrics: $($_.Exception.Message)" -Level "ERROR"
        return $null
    }
}

# ═══════════════════════════════════════════════════════════════════════
# QUEUE MANAGEMENT (Offline Resilience)
# ═══════════════════════════════════════════════════════════════════════

function Initialize-Queue {
    if (-not (Test-Path $script:Config.QueuePath)) {
        New-Item -Path $script:Config.QueuePath -ItemType Directory -Force | Out-Null
        Write-SentinelLog "Initialized beacon queue at $($script:Config.QueuePath)" -Level "INFO"
    }
}

function Add-ToQueue {
    param([hashtable]$Metrics)
    
    try {
        $queueFiles = Get-ChildItem -Path $script:Config.QueuePath -Filter "*.json"
        
        # Check queue size limit
        if ($queueFiles.Count -ge $script:Config.MaxQueueSize) {
            # Remove oldest entry
            $oldest = $queueFiles | Sort-Object CreationTime | Select-Object -First 1
            Remove-Item -Path $oldest.FullName -Force
            Write-SentinelLog "Queue full, removed oldest entry" -Level "WARN"
        }
        
        $filename = "beacon_$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds()).json"
        $filepath = Join-Path $script:Config.QueuePath $filename
        
        $Metrics | ConvertTo-Json -Depth 10 | Out-File -FilePath $filepath -Encoding UTF8
        Write-SentinelLog "Queued beacon for later transmission" -Level "INFO"
        
    } catch {
        Write-SentinelLog "Failed to queue beacon: $($_.Exception.Message)" -Level "ERROR"
    }
}

function Send-QueuedBeacons {
    try {
        $queueFiles = Get-ChildItem -Path $script:Config.QueuePath -Filter "*.json" | Sort-Object CreationTime
        
        if ($queueFiles.Count -eq 0) {
            return
        }
        
        Write-SentinelLog "Processing $($queueFiles.Count) queued beacons" -Level "INFO"
        
        foreach ($file in $queueFiles) {
            try {
                $metrics = Get-Content -Path $file.FullName -Raw | ConvertFrom-Json -AsHashtable
                
                if (Send-BeaconHTTP -Metrics $metrics) {
                    Remove-Item -Path $file.FullName -Force
                    Write-SentinelLog "Sent queued beacon from $($file.Name)" -Level "INFO"
                } else {
                    # Failed to send, keep in queue
                    break
                }
                
            } catch {
                Write-SentinelLog "Failed to process queued beacon $($file.Name): $($_.Exception.Message)" -Level "ERROR"
                Remove-Item -Path $file.FullName -Force  # Remove corrupted file
            }
        }
        
    } catch {
        Write-SentinelLog "Error processing queue: $($_.Exception.Message)" -Level "ERROR"
    }
}

# ═══════════════════════════════════════════════════════════════════════
# BEACON TRANSMISSION
# ═══════════════════════════════════════════════════════════════════════

function Send-BeaconHTTP {
    param([hashtable]$Metrics)

    $retryCount = 0
    $headers = @{
        "Content-Type" = "application/json"
    }

    # Add API key if configured
    if ($script:Config.APIKey) {
        $headers["X-API-Key"] = $script:Config.APIKey
    }

    while ($retryCount -lt $script:Config.MaxRetries) {
        try {
            $json = $Metrics | ConvertTo-Json -Depth 10 -Compress

            $response = Invoke-RestMethod -Uri $script:Config.APIEndpoint `
                                         -Method POST `
                                         -Body $json `
                                         -Headers $headers `
                                         -TimeoutSec $script:Config.RequestTimeout `
                                         -ErrorAction Stop

            Write-SentinelLog "Beacon transmitted successfully via HTTP" -Level "DEBUG"
            return $true

        } catch {
            $statusCode = $_.Exception.Response.StatusCode.value__

            if ($statusCode -eq 401) {
                Write-SentinelLog "Authentication failed - check HBV_API_KEY configuration" -Level "ERROR"
                return $false
            }
            elseif ($statusCode -eq 429) {
                Write-SentinelLog "Rate limited by collector, will retry later" -Level "WARN"
                return $false
            }

            $retryCount++
            Write-SentinelLog "HTTP beacon failed (attempt $retryCount/$($script:Config.MaxRetries)): $($_.Exception.Message)" -Level "WARN"

            if ($retryCount -lt $script:Config.MaxRetries) {
                Start-Sleep -Seconds $script:Config.RetryDelay
            }
        }
    }

    Write-SentinelLog "All HTTP beacon attempts failed" -Level "ERROR"
    return $false
}

function Send-Beacon {
    param([hashtable]$Metrics)
    
    # Try to send via HTTP
    if (Send-BeaconHTTP -Metrics $Metrics) {
        # Success - also try to send any queued beacons
        Send-QueuedBeacons
        return $true
    } else {
        # Failed - queue for later
        Add-ToQueue -Metrics $Metrics
        return $false
    }
}

# ═══════════════════════════════════════════════════════════════════════
# MAIN BEACON LOOP
# ═══════════════════════════════════════════════════════════════════════

function Start-SentinelAgent {
    Write-Host ""
    Write-Host "╔═══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "║  🦡 HoneyBadger Sentinel Agent - Starting...             ║" -ForegroundColor Cyan
    Write-Host "╚═══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
    Write-Host ""

    Write-SentinelLog "HoneyBadger Sentinel Agent v1.1.0" -Level "INFO"
    Write-SentinelLog "Agent ID: $($script:Config.AgentID)" -Level "INFO"
    Write-SentinelLog "Collector: $($script:Config.APIEndpoint)" -Level "INFO"
    Write-SentinelLog "Beacon Interval: $($script:Config.BeaconInterval)s" -Level "INFO"

    if ($script:Config.APIKey) {
        Write-SentinelLog "API Key: configured" -Level "INFO"
    } else {
        Write-SentinelLog "API Key: not configured (set HBV_API_KEY if required)" -Level "INFO"
    }

    # Register Ctrl+C handler for graceful shutdown
    $null = Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action {
        $script:ShutdownRequested = $true
    }

    try {
        [Console]::TreatControlCAsInput = $false
        $null = [Console]::CancelKeyPress.Add({
            param($sender, $e)
            $e.Cancel = $true
            $script:ShutdownRequested = $true
            Write-Host "`n[*] Shutdown requested..." -ForegroundColor Yellow
        })
    } catch {
        # Console events may not be available in all contexts
    }

    # Initialize queue
    Initialize-Queue

    # Try to send any queued beacons from previous sessions
    Send-QueuedBeacons

    Write-Host ""
    Write-Host "[*] Agent running - Press Ctrl+C to stop" -ForegroundColor Yellow
    Write-Host "[*] Logs: $($script:Config.LogPath)" -ForegroundColor Gray
    Write-Host ""

    $beaconCount = 0

    while (-not $script:ShutdownRequested) {
        try {
            $beaconCount++
            Write-SentinelLog "=== Beacon #$beaconCount ===" -Level "INFO"

            # Collect metrics
            $metrics = Get-SystemMetrics

            if ($metrics) {
                # Send beacon
                $success = Send-Beacon -Metrics $metrics

                if ($success) {
                    Write-Host "[✓] Beacon #$beaconCount transmitted" -ForegroundColor Green
                } else {
                    Write-Host "[!] Beacon #$beaconCount queued (collector offline)" -ForegroundColor Yellow
                }
            } else {
                Write-SentinelLog "Failed to collect metrics, skipping beacon" -Level "ERROR"
            }

            # Wait for next beacon interval (interruptible)
            for ($i = 0; $i -lt $script:Config.BeaconInterval; $i++) {
                if ($script:ShutdownRequested) { break }
                Start-Sleep -Seconds 1
            }

        } catch {
            Write-SentinelLog "Beacon loop error: $($_.Exception.Message)" -Level "ERROR"
            Start-Sleep -Seconds 10
        }
    }

    Write-Host "`n[*] Shutting down Sentinel agent..." -ForegroundColor Yellow
    Write-SentinelLog "Agent stopped gracefully" -Level "INFO"
}

# ═══════════════════════════════════════════════════════════════════════
# INSTALLATION & SERVICE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════

function Install-SentinelService {
    <#
    .SYNOPSIS
        Install Sentinel as a scheduled task
    #>
    
    Write-Host "[*] Installing HoneyBadger Sentinel as scheduled task..." -ForegroundColor Cyan
    
    $scriptPath = $PSCommandPath
    $taskName = "HoneyBadger-Sentinel"
    
    # Check if task already exists
    $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existingTask) {
        Write-Host "[!] Task already exists, removing..." -ForegroundColor Yellow
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
    
    # Create scheduled task
    $action = New-ScheduledTaskAction -Execute "pwsh.exe" -Argument "-NoProfile -WindowStyle Hidden -File `"$scriptPath`" -RunAgent"
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
    
    Write-Host "[✓] Sentinel installed as scheduled task: $taskName" -ForegroundColor Green
    Write-Host "[*] Task will start automatically at boot" -ForegroundColor Gray
    Write-Host "[*] To start now: Start-ScheduledTask -TaskName '$taskName'" -ForegroundColor Gray
}

function Uninstall-SentinelService {
    <#
    .SYNOPSIS
        Uninstall Sentinel scheduled task
    #>
    
    $taskName = "HoneyBadger-Sentinel"
    
    $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existingTask) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "[✓] Sentinel uninstalled" -ForegroundColor Green
    } else {
        Write-Host "[!] Sentinel task not found" -ForegroundColor Yellow
    }
}

# ═══════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════

# Command-line parameters
param(
    [switch]$RunAgent,
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Test
)

if ($Install) {
    Install-SentinelService
}
elseif ($Uninstall) {
    Uninstall-SentinelService
}
elseif ($Test) {
    Write-Host "[*] Testing metrics collection..." -ForegroundColor Cyan
    $metrics = Get-SystemMetrics
    $metrics | ConvertTo-Json -Depth 10 | Write-Host
    Write-Host "`n[✓] Metrics test complete" -ForegroundColor Green
}
elseif ($RunAgent) {
    Start-SentinelAgent
}
else {
    Write-Host ""
    Write-Host "╔═══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "║  🦡 HoneyBadger Sentinel Agent v1.1.0                    ║" -ForegroundColor Cyan
    Write-Host "╚═══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Usage:" -ForegroundColor Yellow
    Write-Host "  .\Sentinel-Agent-Windows.ps1 -RunAgent      # Run agent interactively" -ForegroundColor Gray
    Write-Host "  .\Sentinel-Agent-Windows.ps1 -Install       # Install as scheduled task" -ForegroundColor Gray
    Write-Host "  .\Sentinel-Agent-Windows.ps1 -Uninstall     # Remove scheduled task" -ForegroundColor Gray
    Write-Host "  .\Sentinel-Agent-Windows.ps1 -Test          # Test metrics collection" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Environment Variables:" -ForegroundColor Yellow
    Write-Host "  HBV_COLLECTOR_URL    - Collector endpoint URL" -ForegroundColor Gray
    Write-Host "  HBV_API_KEY          - API key for authentication" -ForegroundColor Gray
    Write-Host "  HBV_BEACON_INTERVAL  - Beacon interval in seconds (default: 30)" -ForegroundColor Gray
    Write-Host "  HBV_LOG_LEVEL        - Logging level (DEBUG/INFO/WARN/ERROR)" -ForegroundColor Gray
    Write-Host ""
}
