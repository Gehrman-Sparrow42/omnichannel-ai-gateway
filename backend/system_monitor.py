import os
import shutil
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from collections import deque

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

_boot_time = time.time()
_recent_logs: deque = deque(maxlen=150)


def add_system_log(
    level: str,
    title: str,
    message: str,
    branch_id: Optional[str] = None,
    channel: Optional[str] = None,
):
    """Appends an event into the live system event ring buffer."""
    entry = {
        "id": f"{int(time.time() * 1000)}-{len(_recent_logs)}",
        "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        "iso": datetime.now(timezone.utc).isoformat(),
        "level": level.lower(),  # 'info', 'success', 'warn', 'error'
        "title": title,
        "message": message,
        "branch_id": branch_id or "global",
        "channel": channel or "system",
    }
    _recent_logs.append(entry)


def get_recent_system_logs(
    level: Optional[str] = None,
    branch_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Returns chronologically ordered system logs."""
    logs = list(_recent_logs)
    if level and level != "all":
        logs = [l for l in logs if l["level"] == level.lower()]
    if branch_id and branch_id != "all":
        logs = [l for l in logs if l["branch_id"] == branch_id.lower()]
    return logs[-limit:]


def clear_system_logs():
    """Clears the event log buffer."""
    _recent_logs.clear()


def get_system_metrics() -> Dict[str, Any]:
    """Retrieves real-time CPU, RAM, disk, and process uptime metrics."""
    metrics: Dict[str, Any] = {
        "cpu_percent": 0.0,
        "ram": {
            "used_mb": 0,
            "total_mb": 0,
            "percent": 0.0,
            "display": "0 / 0 MB",
        },
        "disk": {
            "used_gb": 0.0,
            "total_gb": 0.0,
            "percent": 0.0,
            "display": "0 / 0 GB",
        },
        "uptime": "0s",
        "uptime_seconds": int(time.time() - _boot_time),
    }

    # 1. Disk usage
    try:
        drive_path = "C:\\" if os.name == "nt" else "/"
        total, used, free = shutil.disk_usage(drive_path)
        total_gb = round(total / (1024 ** 3), 1)
        used_gb = round(used / (1024 ** 3), 1)
        percent = round((used / total) * 100, 1) if total > 0 else 0.0
        metrics["disk"] = {
            "used_gb": used_gb,
            "total_gb": total_gb,
            "percent": percent,
            "display": f"{used_gb} / {total_gb} GB",
        }
    except Exception:
        pass

    # 2. psutil metrics
    if HAS_PSUTIL:
        try:
            cpu_val = psutil.cpu_percent(interval=None)
            metrics["cpu_percent"] = round(float(cpu_val if isinstance(cpu_val, (int, float)) else cpu_val[0]), 1)
            vm = psutil.virtual_memory()
            used_mb = int(vm.used / (1024 ** 2))
            total_mb = int(vm.total / (1024 ** 2))
            metrics["ram"] = {
                "used_mb": used_mb,
                "total_mb": total_mb,
                "percent": round(vm.percent, 1),
                "display": f"{used_mb} / {total_mb} MB",
            }

            uptime_sec = int(time.time() - _boot_time)
            hours, remainder = divmod(uptime_sec, 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                metrics["uptime"] = f"{hours}sa {minutes}dk"
            elif minutes > 0:
                metrics["uptime"] = f"{minutes}dk {seconds}sn"
            else:
                metrics["uptime"] = f"{seconds}sn"
        except Exception:
            pass

    return metrics
