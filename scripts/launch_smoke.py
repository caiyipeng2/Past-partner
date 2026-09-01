"""Run a bounded health/API smoke against each supported server surface.

The smoke runner owns only the processes it starts and always terminates them.
Compose is treated as an external disposable surface: each run gets a unique
project name and a temporary master key, then ``down --volumes`` removes only
that project's resources. A missing Docker executable is reported clearly.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
HEALTH_PATH = "/api/v1/health"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(port: int, process: subprocess.Popen[str], timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}{HEALTH_PATH}"
    last_error = "service did not start"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(f"process exited with {process.returncode}: {output[-500:]}")
        try:
            with urlopen(url, timeout=1) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if response.status == 200 and payload.get("status") == "healthy":
                    return payload
                last_error = f"unexpected health response: {response.status}"
        except Exception as exc:  # startup races include refused sockets and incomplete headers
            last_error = str(exc)
        time.sleep(0.15)
    raise TimeoutError(last_error)


def _start(command: list[str], *, port: int, data_dir: Path) -> dict:
    env = os.environ.copy()
    env.update(
        {
            "PAST_PARTNER_MODE": "development",
            "PAST_PARTNER_HOST": "127.0.0.1",
            "PAST_PARTNER_PORT": str(port),
            "PAST_PARTNER_DATA_DIR": str(data_dir),
        }
    )
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        return _wait_for_health(port, process)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _cli_command() -> list[str]:
    executable_name = "companion-server.exe" if os.name == "nt" else "companion-server"
    interpreter_adjacent = Path(sys.executable).with_name(executable_name)
    if interpreter_adjacent.is_file():
        return [str(interpreter_adjacent)]
    executable = shutil.which("companion-server")
    if executable is None:
        raise RuntimeError("companion-server is not installed; run python -m pip install -e .")
    return [executable]


def _surface_commands() -> dict[str, list[str]]:
    return {
        "module": [sys.executable, "-m", "src.server"],
        "npm": ["npm.cmd" if os.name == "nt" else "npm", "start", "--"],
    }


def _run_compose(port: int) -> dict:
    executable = shutil.which("docker")
    if executable is None:
        raise RuntimeError("Docker executable not found; install Docker Desktop to run compose smoke")
    env = os.environ.copy()
    env["PAST_PARTNER_COMPOSE_PORT"] = str(port)
    env["PAST_PARTNER_MASTER_KEY"] = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    env["PAST_PARTNER_MASTER_KEY_SOURCE"] = "environment"
    project_name = f"past-partner-smoke-{uuid4().hex[:12]}"
    base = [executable, "compose", "-p", project_name, "-f", str(ROOT / "compose.yaml")]
    try:
        subprocess.run(
            base + ["up", "--build", "--detach"],
            cwd=ROOT,
            env=env,
            check=True,
            timeout=180,
        )
        deadline = time.monotonic() + 30
        last_error = "compose service did not become healthy"
        while time.monotonic() < deadline:
            try:
                with urlopen(f"http://127.0.0.1:{port}{HEALTH_PATH}", timeout=2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    if response.status == 200 and payload.get("status") == "healthy":
                        return payload
                    last_error = f"unexpected compose health response: {response.status}"
            except Exception as exc:
                last_error = str(exc)
            time.sleep(0.5)
        raise TimeoutError(last_error)
    finally:
        subprocess.run(
            base + ["down", "--volumes", "--remove-orphans"],
            cwd=ROOT,
            env=env,
            check=False,
            timeout=60,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Smoke test module, CLI, npm, and Docker Compose launch surfaces"
    )
    parser.add_argument(
        "--surface",
        action="append",
        choices=("module", "cli", "npm", "compose"),
        help="surface to test; repeat to select a subset (default: all)",
    )
    args = parser.parse_args(argv)
    surfaces = args.surface or ["module", "cli", "npm", "compose"]
    results: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="past-partner-smoke-") as temp:
        for surface in surfaces:
            port = _free_port()
            try:
                if surface == "compose":
                    results[surface] = _run_compose(port)
                else:
                    base_command = _cli_command() if surface == "cli" else _surface_commands()[surface]
                    command = base_command + [
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--data-dir",
                        temp,
                    ]
                    results[surface] = _start(command, port=port, data_dir=Path(temp))
            except Exception as exc:
                print(f"{surface}: failed: {exc}", file=sys.stderr)
                return 1
    print(json.dumps(results, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
