#!/usr/bin/env python3
"""
HoneyBadger Sentinel - Linux Agent

Lightweight monitoring agent that beacons system metrics to central collector.
C2-style architecture with MQTT and HTTP fallback.

Author: HoneyBadger
Version: 1.1.0
CyberShield 2026 - Infrastructure Monitoring
"""

import os
import sys
import json
import time
import socket
import logging
import signal
import requests
import psutil
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, List

# ═══════════════════════════════════════════════════════════════════════
# CONFIGURATION (with environment variable support)
# ═══════════════════════════════════════════════════════════════════════

def get_env(key: str, default: str) -> str:
    """Get environment variable with default fallback."""
    return os.environ.get(key, default)

def get_env_int(key: str, default: int) -> int:
    """Get integer environment variable with default fallback."""
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default

def get_env_bool(key: str, default: bool) -> bool:
    """Get boolean environment variable with default fallback."""
    return os.environ.get(key, str(default)).lower() in ('true', '1', 'yes')

CONFIG = {
    # Agent Identity
    "agent_id": get_env("HBV_AGENT_ID", socket.gethostname()),
    "agent_type": "linux",

    # Collector Configuration
    "api_endpoint": get_env("HBV_COLLECTOR_URL", "http://<COLLECTOR_IP>:8443/api/beacon"),
    "api_key": get_env("HBV_API_KEY", ""),

    # Beacon Settings
    "beacon_interval": get_env_int("HBV_BEACON_INTERVAL", 30),
    "max_retries": get_env_int("HBV_MAX_RETRIES", 3),
    "retry_delay": get_env_int("HBV_RETRY_DELAY", 5),
    "request_timeout": get_env_int("HBV_REQUEST_TIMEOUT", 10),

    # Queue Settings (offline resilience)
    "queue_path": get_env("HBV_QUEUE_PATH", "/tmp/hbv-sentinel-queue"),
    "max_queue_size": get_env_int("HBV_MAX_QUEUE_SIZE", 100),

    # Metrics Collection
    "collect_cpu": get_env_bool("HBV_COLLECT_CPU", True),
    "collect_memory": get_env_bool("HBV_COLLECT_MEMORY", True),
    "collect_disk": get_env_bool("HBV_COLLECT_DISK", True),
    "collect_network": get_env_bool("HBV_COLLECT_NETWORK", True),
    "collect_temperature": get_env_bool("HBV_COLLECT_TEMPERATURE", True),
    "collect_raid": get_env_bool("HBV_COLLECT_RAID", True),

    # Logging
    "log_path": get_env("HBV_LOG_PATH", "/var/log/hbv-sentinel.log"),
    "log_level": get_env("HBV_LOG_LEVEL", "INFO")
}

# Graceful shutdown flag
shutdown_requested = False

# ═══════════════════════════════════════════════════════════════════════
# LOGGING SETUP
# ═══════════════════════════════════════════════════════════════════════

def setup_logging():
    """Configure logging with color output"""
    
    # Create formatter
    class ColoredFormatter(logging.Formatter):
        COLORS = {
            'DEBUG': '\033[0;37m',    # Gray
            'INFO': '\033[0;36m',     # Cyan
            'WARNING': '\033[0;33m',  # Yellow
            'ERROR': '\033[0;31m',    # Red
            'CRITICAL': '\033[1;31m', # Bold Red
            'RESET': '\033[0m'
        }
        
        def format(self, record):
            log_color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
            record.levelname = f"{log_color}{record.levelname}{self.COLORS['RESET']}"
            return super().format(record)
    
    # Console handler with colors
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(ColoredFormatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    
    # File handler
    try:
        file_handler = logging.FileHandler(CONFIG['log_path'])
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
    except PermissionError:
        file_handler = None
    
    # Setup logger
    logger = logging.getLogger('HBV-Sentinel')
    logger.setLevel(getattr(logging, CONFIG['log_level']))
    logger.addHandler(console_handler)
    if file_handler:
        logger.addHandler(file_handler)
    
    return logger

logger = setup_logging()

# ═══════════════════════════════════════════════════════════════════════
# METRICS COLLECTION
# ═══════════════════════════════════════════════════════════════════════

def get_cpu_temperature() -> Optional[float]:
    """Get CPU temperature (if available)"""
    try:
        # Try multiple methods
        
        # Method 1: psutil sensors (RPi, most Linux)
        if hasattr(psutil, "sensors_temperatures"):
            temps = psutil.sensors_temperatures()
            if temps:
                # Try common sensor names
                for name in ['coretemp', 'cpu_thermal', 'soc_thermal']:
                    if name in temps:
                        return round(temps[name][0].current, 1)
        
        # Method 2: RPi specific
        if os.path.exists('/sys/class/thermal/thermal_zone0/temp'):
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = float(f.read().strip()) / 1000.0
                return round(temp, 1)
        
    except Exception as e:
        logger.debug(f"Failed to read temperature: {e}")
    
    return None

def get_raid_status() -> Optional[Dict]:
    """Get RAID array status (NAS-specific)"""
    try:
        if os.path.exists('/proc/mdstat'):
            with open('/proc/mdstat', 'r') as f:
                mdstat = f.read()
            
            # Parse mdstat for health
            if 'md0' in mdstat:
                lines = mdstat.split('\n')
                for i, line in enumerate(lines):
                    if 'md0' in line:
                        # Get status from next line
                        if i + 1 < len(lines):
                            status_line = lines[i + 1]
                            if 'active' in status_line.lower():
                                return {
                                    "array": "md0",
                                    "status": "healthy",
                                    "details": status_line.strip()
                                }
                            else:
                                return {
                                    "array": "md0",
                                    "status": "degraded",
                                    "details": status_line.strip()
                                }
    except Exception as e:
        logger.debug(f"Failed to read RAID status: {e}")
    
    return None

def get_system_metrics() -> Optional[Dict]:
    """Collect system metrics"""
    try:
        metrics = {
            "timestamp": int(time.time()),
            "agent_id": CONFIG['agent_id'],
            "agent_type": CONFIG['agent_type']
        }
        
        # CPU Usage
        if CONFIG['collect_cpu']:
            metrics['cpu_percent'] = round(psutil.cpu_percent(interval=1), 2)
            metrics['cpu_count'] = psutil.cpu_count()
        
        # Memory Usage
        if CONFIG['collect_memory']:
            mem = psutil.virtual_memory()
            metrics['memory_total_mb'] = round(mem.total / 1024 / 1024, 2)
            metrics['memory_used_mb'] = round(mem.used / 1024 / 1024, 2)
            metrics['memory_percent'] = round(mem.percent, 2)
        
        # Disk Usage (root filesystem)
        if CONFIG['collect_disk']:
            disk = psutil.disk_usage('/')
            metrics['disk_total_gb'] = round(disk.total / 1024 / 1024 / 1024, 2)
            metrics['disk_used_gb'] = round(disk.used / 1024 / 1024 / 1024, 2)
            metrics['disk_percent'] = round(disk.percent, 2)
        
        # Network Stats
        if CONFIG['collect_network']:
            net_io = psutil.net_io_counters()
            metrics['network_bytes_sent'] = net_io.bytes_sent
            metrics['network_bytes_recv'] = net_io.bytes_recv
        
        # Temperature
        if CONFIG['collect_temperature']:
            temp = get_cpu_temperature()
            if temp:
                metrics['cpu_temp_c'] = temp
        
        # RAID Status (NAS)
        if CONFIG['collect_raid']:
            raid = get_raid_status()
            if raid:
                metrics['raid'] = raid
        
        # System Uptime
        metrics['uptime_seconds'] = int(time.time() - psutil.boot_time())
        
        # Load Average
        metrics['load_average'] = list(os.getloadavg())
        
        logger.debug(f"Collected metrics: CPU {metrics.get('cpu_percent')}%, "
                    f"Memory {metrics.get('memory_percent')}%, "
                    f"Disk {metrics.get('disk_percent')}%")
        
        return metrics
        
    except Exception as e:
        logger.error(f"Error collecting metrics: {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════
# QUEUE MANAGEMENT (Offline Resilience)
# ═══════════════════════════════════════════════════════════════════════

def initialize_queue():
    """Create queue directory if it doesn't exist"""
    queue_path = Path(CONFIG['queue_path'])
    queue_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Initialized beacon queue at {queue_path}")

def add_to_queue(metrics: Dict):
    """Add beacon to offline queue"""
    try:
        queue_path = Path(CONFIG['queue_path'])
        
        # Check queue size limit
        queue_files = list(queue_path.glob('beacon_*.json'))
        if len(queue_files) >= CONFIG['max_queue_size']:
            # Remove oldest entry
            oldest = min(queue_files, key=lambda p: p.stat().st_ctime)
            oldest.unlink()
            logger.warning(f"Queue full, removed oldest entry")
        
        # Save new beacon
        filename = f"beacon_{int(time.time())}.json"
        filepath = queue_path / filename
        
        with open(filepath, 'w') as f:
            json.dump(metrics, f)
        
        logger.info(f"Queued beacon for later transmission")
        
    except Exception as e:
        logger.error(f"Failed to queue beacon: {e}")

def send_queued_beacons() -> int:
    """Send all queued beacons, return number sent"""
    try:
        queue_path = Path(CONFIG['queue_path'])
        queue_files = sorted(queue_path.glob('beacon_*.json'), 
                           key=lambda p: p.stat().st_ctime)
        
        if not queue_files:
            return 0
        
        logger.info(f"Processing {len(queue_files)} queued beacons")
        sent_count = 0
        
        for filepath in queue_files:
            try:
                with open(filepath, 'r') as f:
                    metrics = json.load(f)
                
                if send_beacon_http(metrics):
                    filepath.unlink()
                    sent_count += 1
                    logger.info(f"Sent queued beacon from {filepath.name}")
                else:
                    # Failed to send, stop processing queue
                    break
                    
            except Exception as e:
                logger.error(f"Failed to process queued beacon {filepath.name}: {e}")
                filepath.unlink()  # Remove corrupted file
        
        return sent_count
        
    except Exception as e:
        logger.error(f"Error processing queue: {e}")
        return 0

# ═══════════════════════════════════════════════════════════════════════
# BEACON TRANSMISSION
# ═══════════════════════════════════════════════════════════════════════

def send_beacon_http(metrics: Dict) -> bool:
    """Send beacon via HTTP POST."""
    headers = {'Content-Type': 'application/json'}

    # Add API key if configured
    if CONFIG['api_key']:
        headers['X-API-Key'] = CONFIG['api_key']

    for attempt in range(1, CONFIG['max_retries'] + 1):
        try:
            response = requests.post(
                CONFIG['api_endpoint'],
                json=metrics,
                timeout=CONFIG['request_timeout'],
                headers=headers
            )

            if response.status_code == 200:
                logger.debug("Beacon transmitted successfully via HTTP")
                return True
            elif response.status_code == 401:
                logger.error("Authentication failed - check HBV_API_KEY configuration")
                return False
            elif response.status_code == 429:
                logger.warning("Rate limited by collector, will retry later")
                return False
            else:
                logger.warning(f"HTTP beacon failed with status {response.status_code}")

        except requests.exceptions.Timeout:
            logger.warning(f"HTTP beacon timed out (attempt {attempt}/{CONFIG['max_retries']})")
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Connection error (attempt {attempt}/{CONFIG['max_retries']}): {e}")
        except requests.exceptions.RequestException as e:
            logger.warning(f"HTTP beacon failed (attempt {attempt}/{CONFIG['max_retries']}): {e}")

        if attempt < CONFIG['max_retries']:
            time.sleep(CONFIG['retry_delay'])

    logger.error("All HTTP beacon attempts failed")
    return False

def send_beacon(metrics: Dict) -> bool:
    """Send beacon with fallback and queuing"""
    
    # Try to send via HTTP
    if send_beacon_http(metrics):
        # Success - also try to send any queued beacons
        send_queued_beacons()
        return True
    else:
        # Failed - queue for later
        add_to_queue(metrics)
        return False

# ═══════════════════════════════════════════════════════════════════════
# MAIN BEACON LOOP
# ═══════════════════════════════════════════════════════════════════════

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested
    signal_name = signal.Signals(signum).name
    logger.info(f"Received {signal_name}, initiating graceful shutdown...")
    shutdown_requested = True


def start_sentinel_agent():
    """Main agent loop."""
    global shutdown_requested

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    print("\n╔═══════════════════════════════════════════════════════════╗")
    print("║  🦡 HoneyBadger Sentinel Agent - Starting...             ║")
    print("╚═══════════════════════════════════════════════════════════╝\n")

    logger.info("HoneyBadger Sentinel Agent v1.1.0")
    logger.info(f"Agent ID: {CONFIG['agent_id']}")
    logger.info(f"Collector: {CONFIG['api_endpoint']}")
    logger.info(f"Beacon Interval: {CONFIG['beacon_interval']}s")
    if CONFIG['api_key']:
        logger.info("API Key: configured")
    else:
        logger.info("API Key: not configured (set HBV_API_KEY if required)")

    # Initialize queue
    initialize_queue()

    # Try to send any queued beacons from previous sessions
    sent_count = send_queued_beacons()
    if sent_count > 0:
        logger.info(f"Sent {sent_count} queued beacons from previous session")

    print("\n[*] Agent running - Press Ctrl+C to stop")
    print(f"[*] Logs: {CONFIG['log_path']}\n")

    beacon_count = 0

    try:
        while not shutdown_requested:
            beacon_count += 1
            logger.info(f"=== Beacon #{beacon_count} ===")

            # Collect metrics
            metrics = get_system_metrics()

            if metrics:
                # Send beacon
                success = send_beacon(metrics)

                if success:
                    print(f"[✓] Beacon #{beacon_count} transmitted")
                else:
                    print(f"[!] Beacon #{beacon_count} queued (collector offline)")
            else:
                logger.error("Failed to collect metrics, skipping beacon")

            # Wait for next beacon interval (interruptible)
            for _ in range(CONFIG['beacon_interval']):
                if shutdown_requested:
                    break
                time.sleep(1)

    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"Fatal error in beacon loop: {e}")
        sys.exit(1)

    print("\n\n[*] Shutting down Sentinel agent...")
    logger.info("Agent stopped gracefully")
    sys.exit(0)

# ═══════════════════════════════════════════════════════════════════════
# SYSTEMD SERVICE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════

SYSTEMD_SERVICE = """[Unit]
Description=HoneyBadger Sentinel Monitoring Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 {script_path}
Restart=always
RestartSec=10
User=root
# Load environment from config file if present
EnvironmentFile=-/etc/hbv-sentinel/agent.env
# Graceful shutdown
TimeoutStopSec=30
KillMode=mixed
KillSignal=SIGTERM

[Install]
WantedBy=multi-user.target
"""

def install_service():
    """Install as systemd service."""
    if os.geteuid() != 0:
        print("[!] Must run as root to install service")
        sys.exit(1)

    script_path = os.path.abspath(__file__)
    service_content = SYSTEMD_SERVICE.format(script_path=script_path)

    service_path = Path('/etc/systemd/system/hbv-sentinel.service')
    env_dir = Path('/etc/hbv-sentinel')
    env_file = env_dir / 'agent.env'

    print("[*] Installing HoneyBadger Sentinel as systemd service...")

    # Create environment file directory
    env_dir.mkdir(parents=True, exist_ok=True)

    # Create sample environment file if it doesn't exist
    if not env_file.exists():
        sample_env = """# HoneyBadger Sentinel Agent Configuration
# Uncomment and modify as needed

# Collector URL
# HBV_COLLECTOR_URL=http://<COLLECTOR_IP>:8443/api/beacon

# API Key (if collector requires authentication)
# HBV_API_KEY=your-api-key-here

# Beacon interval in seconds
# HBV_BEACON_INTERVAL=30

# Log level (DEBUG, INFO, WARNING, ERROR)
# HBV_LOG_LEVEL=INFO
"""
        with open(env_file, 'w') as f:
            f.write(sample_env)
        os.chmod(env_file, 0o600)  # Restrict permissions for security
        print(f"[*] Created sample config: {env_file}")

    with open(service_path, 'w') as f:
        f.write(service_content)

    os.system('systemctl daemon-reload')
    os.system('systemctl enable hbv-sentinel.service')

    print("[✓] Sentinel installed as systemd service: hbv-sentinel")
    print("[*] Start with: systemctl start hbv-sentinel")
    print("[*] Check status: systemctl status hbv-sentinel")
    print("[*] View logs: journalctl -u hbv-sentinel -f")
    print(f"[*] Config file: {env_file}")

def uninstall_service():
    """Uninstall systemd service"""
    if os.geteuid() != 0:
        print("[!] Must run as root to uninstall service")
        sys.exit(1)
    
    service_path = Path('/etc/systemd/system/hbv-sentinel.service')
    
    if service_path.exists():
        os.system('systemctl stop hbv-sentinel.service')
        os.system('systemctl disable hbv-sentinel.service')
        service_path.unlink()
        os.system('systemctl daemon-reload')
        print("[✓] Sentinel uninstalled")
    else:
        print("[!] Sentinel service not found")

# ═══════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='HoneyBadger Sentinel Agent v1.0.0')
    parser.add_argument('--install', action='store_true', help='Install as systemd service')
    parser.add_argument('--uninstall', action='store_true', help='Uninstall systemd service')
    parser.add_argument('--test', action='store_true', help='Test metrics collection')
    
    args = parser.parse_args()
    
    if args.install:
        install_service()
    elif args.uninstall:
        uninstall_service()
    elif args.test:
        print("[*] Testing metrics collection...")
        metrics = get_system_metrics()
        print(json.dumps(metrics, indent=2))
        print("\n[✓] Metrics test complete")
    else:
        start_sentinel_agent()
