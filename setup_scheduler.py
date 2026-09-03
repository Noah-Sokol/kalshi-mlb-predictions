"""
Registers update.py as a Windows Task Scheduler job at 9:00 AM every day.

Run once:
    python setup_scheduler.py

To remove the task:
    python setup_scheduler.py --remove

To check if it's registered:
    python setup_scheduler.py --status
"""
import argparse
import subprocess
import sys
from pathlib import Path

TASK_NAME = "KalshiMLBDailyUpdate"
PROJECT_DIR = str(Path(__file__).parent.resolve())
SCRIPT = str(Path(__file__).parent / "update.py")


def _run(cmd: list[str]) -> tuple[int, str]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout + result.stderr


def register(hour: int = 9, minute: int = 0) -> None:
    python_exe = sys.executable

    # Build the PowerShell command to create the task
    ps_script = f"""
$action  = New-ScheduledTaskAction `
    -Execute '{python_exe}' `
    -Argument '{SCRIPT}' `
    -WorkingDirectory '{PROJECT_DIR}'

$trigger = New-ScheduledTaskTrigger -Daily -At '{hour:02d}:{minute:02d}'

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

Register-ScheduledTask `
    -TaskName '{TASK_NAME}' `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description 'MLB Kalshi daily model update and bet logging' `
    -Force
"""

    code, out = _run(["powershell", "-NonInteractive", "-Command", ps_script])
    if code == 0:
        print(f"Task '{TASK_NAME}' registered — will run daily at {hour:02d}:{minute:02d}.")
        print(f"  Python: {python_exe}")
        print(f"  Script: {SCRIPT}")
        print(f"  Working dir: {PROJECT_DIR}")
    else:
        print(f"Failed to register task (exit {code}):\n{out}")
        print("Try running this script as Administrator.")


def remove() -> None:
    code, out = _run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"])
    if code == 0:
        print(f"Task '{TASK_NAME}' removed.")
    else:
        print(f"Could not remove task: {out}")


def status() -> None:
    code, out = _run(["schtasks", "/query", "/tn", TASK_NAME, "/fo", "LIST"])
    if code == 0:
        print(out)
    else:
        print(f"Task '{TASK_NAME}' not found.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--remove", action="store_true", help="Unregister the scheduled task")
    ap.add_argument("--status", action="store_true", help="Show current task status")
    ap.add_argument("--hour",   type=int, default=9,  help="Hour to run (24h, default 9)")
    ap.add_argument("--minute", type=int, default=0,  help="Minute to run (default 0)")
    args = ap.parse_args()

    if args.remove:
        remove()
    elif args.status:
        status()
    else:
        register(args.hour, args.minute)


if __name__ == "__main__":
    main()
