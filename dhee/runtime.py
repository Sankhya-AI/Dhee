"""Local Dhee runtime manager.

The runtime daemon is deliberately small and local-only.  It gives users a
clear answer to "is Dhee running and what venv/process is it using?" while
leaving hot-path acceleration hooks available for later integration.
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def data_dir() -> Path:
    return Path(os.environ.get("DHEE_DATA_DIR") or (_home() / ".dhee")).expanduser()


def runtime_dir(*, create: bool = True) -> Path:
    root = data_dir() / "runtime"
    if create:
        root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(root, 0o700)
        except OSError:
            pass
    return root


def state_path(*, create: bool = True) -> Path:
    return runtime_dir(create=create) / "daemon.json"


def log_path(*, create: bool = True) -> Path:
    return runtime_dir(create=create) / "daemon.log"


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _pid_running(pid: Any) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _managed_venv() -> Dict[str, Any]:
    path = _home() / ".dhee" / ".venv"
    current = Path(sys.prefix).resolve()
    return {
        "path": str(path),
        "exists": path.exists(),
        "current_prefix": str(current),
        "current_is_managed": path.exists() and current == path.resolve(),
        "python": sys.executable,
    }


def _fetch_json(url: str, *, timeout: float = 0.25) -> Dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        raw = response.read(64 * 1024)
    return json.loads(raw.decode("utf-8"))


def _post_json(url: str, payload: Dict[str, Any], *, timeout: float = 2.0) -> Dict[str, Any]:
    raw = json.dumps(payload, default=str).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(1024 * 1024)
    return json.loads(body.decode("utf-8"))


def _active_endpoint() -> Optional[str]:
    if str(os.environ.get("DHEE_RUNTIME_DISABLE") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return None
    state = _read_json(state_path(create=False))
    endpoint = str(state.get("endpoint") or "").strip()
    if not endpoint or not _pid_running(state.get("pid")):
        return None
    if not endpoint.startswith("http://127.0.0.1:") and not endpoint.startswith("http://localhost:"):
        return None
    return endpoint


def execute_shell(
    command: str,
    *,
    repo: Optional[str] = None,
    user_id: str = "default",
    agent_id: str = "client",
    workspace_id: Optional[str] = None,
    timeout: float = 3.0,
) -> Optional[Dict[str, Any]]:
    """Execute a DheeFS shell command through the daemon if it is healthy.

    Returns ``None`` when the daemon is unavailable so callers can fall back to
    the existing in-process path. This keeps the runtime an accelerator, not a
    new hard dependency.
    """
    endpoint = _active_endpoint()
    if not endpoint:
        return None
    payload = {
        "command": command,
        "repo": repo,
        "user_id": user_id,
        "agent_id": agent_id,
        "workspace_id": workspace_id,
    }
    try:
        return _post_json(f"{endpoint}/dheefs/execute", payload, timeout=timeout)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None


def execute_context(
    action: str,
    *,
    repo: Optional[str] = None,
    user_id: str = "default",
    agent_id: str = "client",
    workspace_id: Optional[str] = None,
    args: Optional[Dict[str, Any]] = None,
    timeout: float = 3.0,
) -> Optional[Dict[str, Any]]:
    """Execute a compiled context action through the daemon if it is healthy.

    Returns ``None`` when the daemon is unavailable so callers can retain the
    existing in-process ContextStateStore path.
    """
    endpoint = _active_endpoint()
    if not endpoint:
        return None
    payload = {
        "action": action,
        "repo": repo,
        "user_id": user_id,
        "agent_id": agent_id,
        "workspace_id": workspace_id,
    }
    if args:
        payload.update(args)
    try:
        result = _post_json(f"{endpoint}/context/execute", payload, timeout=timeout)
        if result.get("format") == "dhee_context_error":
            return None
        return result
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None


def execute_router(
    action: str,
    arguments: Dict[str, Any],
    *,
    timeout: float = 3.0,
) -> Optional[Dict[str, Any]]:
    """Execute a pointer-router action through the daemon if healthy.

    Read and grep are always safe to accelerate. Bash is daemonized only when
    the daemon process was started with the server-side bash opt-in and cwd
    allowlist environment variables.
    """
    endpoint = _active_endpoint()
    if not endpoint:
        return None
    normalized_action = str(action or "").strip().lower()
    if normalized_action in {"bash", "dhee_bash"}:
        try:
            requested_timeout = float(arguments.get("timeout", 30.0))
        except (TypeError, ValueError):
            requested_timeout = 30.0
        timeout = max(timeout, min(max(1.0, requested_timeout), 600.0) + 2.0)
    payload = {
        "action": action,
        "arguments": arguments,
    }
    try:
        result = _post_json(f"{endpoint}/router/execute", payload, timeout=timeout)
        if result.get("format") == "dhee_router_error":
            return None
        return result
    except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError):
        return None


def status(*, timeout: float = 0.25) -> Dict[str, Any]:
    started = time.perf_counter()
    state = _read_json(state_path(create=False))
    pid = state.get("pid")
    endpoint = state.get("endpoint")
    pid_alive = _pid_running(pid)
    health: Dict[str, Any] = {"ok": False}
    if pid_alive and endpoint:
        try:
            health = _fetch_json(f"{endpoint}/healthz", timeout=timeout)
            health["ok"] = True
        except (OSError, urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
            health = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    elif state:
        health = {"ok": False, "error": "stale pidfile or daemon not running"}

    running = bool(pid_alive and health.get("ok"))
    if state and not pid_alive:
        state["stale"] = True

    return {
        "daemon": {
            "running": running,
            "pid": pid,
            "pid_alive": pid_alive,
            "endpoint": endpoint,
            "started_at": state.get("started_at"),
            "uptime_seconds": max(0.0, time.time() - float(state.get("started_at") or time.time())) if running else 0.0,
            "health": health,
            "state": state,
        },
        "paths": {
            "data_dir": str(data_dir()),
            "runtime_dir": str(runtime_dir(create=False)),
            "state": str(state_path(create=False)),
            "log": str(log_path(create=False)),
        },
        "venv": _managed_venv(),
        "client": {
            "python": sys.executable,
            "query_ms": round((time.perf_counter() - started) * 1000, 2),
        },
    }


def start_daemon(*, wait: bool = True, timeout: float = 5.0) -> Dict[str, Any]:
    current = status()
    if current["daemon"]["running"]:
        return {"started": False, "reason": "already_running", "status": current}

    stale = state_path(create=False)
    if stale.exists():
        try:
            stale.unlink()
        except OSError:
            pass

    env = os.environ.copy()
    env.setdefault("DHEE_DATA_DIR", str(data_dir()))
    log = log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log.open("ab")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "dhee.runtime_daemon"],
            stdout=log_handle,
            stderr=log_handle,
            stdin=subprocess.DEVNULL,
            env=env,
            cwd=os.getcwd(),
            start_new_session=True,
        )
    finally:
        log_handle.close()

    if not wait:
        return {"started": True, "pid": proc.pid, "status": status()}

    deadline = time.time() + timeout
    last = status()
    while time.time() < deadline:
        last = status(timeout=0.5)
        if last["daemon"]["running"]:
            return {"started": True, "pid": proc.pid, "status": last}
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    return {
        "started": False,
        "pid": proc.pid,
        "error": "daemon did not become healthy before timeout",
        "status": last,
    }


def stop_daemon(*, timeout: float = 3.0) -> Dict[str, Any]:
    before = status()
    pid = before["daemon"].get("pid")
    if not before["daemon"].get("pid_alive"):
        try:
            state_path(create=False).unlink()
        except OSError:
            pass
        return {"stopped": False, "reason": "not_running", "status": status()}

    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return {"stopped": False, "reason": "invalid_pid", "status": before}
    if pid_int == os.getpid():
        return {"stopped": False, "reason": "refusing_to_stop_current_process", "status": before}

    os.kill(pid_int, signal.SIGTERM)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_running(pid_int):
            try:
                state_path(create=False).unlink()
            except OSError:
                pass
            return {"stopped": True, "status": status()}
        time.sleep(0.05)

    try:
        os.kill(pid_int, signal.SIGKILL)
    except OSError:
        pass
    try:
        state_path(create=False).unlink()
    except OSError:
        pass
    return {"stopped": True, "forced": True, "status": status()}


def restart_daemon(*, timeout: float = 5.0) -> Dict[str, Any]:
    stopped = stop_daemon()
    started = start_daemon(timeout=timeout)
    return {"stopped": stopped, "started": started, "status": status()}


def format_status(data: Dict[str, Any]) -> str:
    daemon = data.get("daemon") or {}
    paths = data.get("paths") or {}
    venv = data.get("venv") or {}
    lines = ["Dhee runtime"]
    lines.append(f"  daemon:       {'running' if daemon.get('running') else 'stopped'}")
    if daemon.get("pid"):
        lines.append(f"  pid:          {daemon.get('pid')}")
    if daemon.get("endpoint"):
        lines.append(f"  endpoint:     {daemon.get('endpoint')}")
    health = daemon.get("health") or {}
    if health.get("error"):
        lines.append(f"  health:       {health.get('error')}")
    elif daemon.get("running"):
        lines.append(f"  health:       ok")
    lines.append(f"  data:         {paths.get('data_dir')}")
    lines.append(f"  runtime:      {paths.get('runtime_dir')}")
    lines.append(f"  log:          {paths.get('log')}")
    lines.append(f"  managed venv: {'present' if venv.get('exists') else 'missing'} ({venv.get('path')})")
    lines.append(f"  python:       {venv.get('python')}")
    return "\n".join(lines)


@dataclass
class _DaemonState:
    started_at: float
    host: str
    port: int

    @property
    def endpoint(self) -> str:
        return f"http://{self.host}:{self.port}"


def serve_forever(host: str = "127.0.0.1", port: Optional[int] = None) -> None:
    bind_port = int(port if port is not None else os.environ.get("DHEE_RUNTIME_PORT") or 0)
    httpd = ThreadingHTTPServer((host, bind_port), _Handler)
    actual_port = int(httpd.server_address[1])
    state = _DaemonState(started_at=time.time(), host=host, port=actual_port)
    _Handler.daemon_state = state
    payload = {
        "pid": os.getpid(),
        "host": host,
        "port": actual_port,
        "endpoint": state.endpoint,
        "started_at": state.started_at,
        "python": sys.executable,
        "cwd": os.getcwd(),
    }
    _write_json(state_path(), payload)

    def _cleanup() -> None:
        current = _read_json(state_path(create=False))
        if current.get("pid") == os.getpid():
            try:
                state_path(create=False).unlink()
            except OSError:
                pass

    def _shutdown(_signum: int, _frame: Any) -> None:
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    atexit.register(_cleanup)
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    try:
        httpd.serve_forever(poll_interval=0.25)
    finally:
        httpd.server_close()
        _cleanup()


class _Handler(BaseHTTPRequestHandler):
    daemon_state: Optional[_DaemonState] = None

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _send(self, code: int, payload: Dict[str, Any]) -> None:
        raw = json.dumps(payload, indent=2, sort_keys=True, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        state = self.daemon_state
        if self.path in {"/healthz", "/health"}:
            self._send(
                200,
                {
                    "status": "ok",
                    "pid": os.getpid(),
                    "uptime_seconds": max(0.0, time.time() - (state.started_at if state else time.time())),
                    "bash": _bash_runtime_status(),
                },
            )
            return
        if self.path == "/status":
            self._send(
                200,
                {
                    "status": "ok",
                    "pid": os.getpid(),
                    "endpoint": state.endpoint if state else None,
                    "started_at": state.started_at if state else None,
                    "data_dir": str(data_dir()),
                    "runtime_dir": str(runtime_dir()),
                    "python": sys.executable,
                    "bash": _bash_runtime_status(),
                },
            )
            return
        self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.client_address and self.client_address[0] not in {"127.0.0.1", "::1"}:
            self._send(403, {"error": "forbidden"})
            return
        if self.path not in {"/dheefs/execute", "/context/execute", "/router/execute"}:
            self._send(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._send(400, {"error": "invalid_content_length"})
            return
        if length <= 0 or length > 1024 * 1024:
            self._send(400, {"error": "invalid_request_size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:
            self._send(400, {"error": f"invalid_json: {type(exc).__name__}: {exc}"})
            return
        if self.path == "/dheefs/execute":
            result = _execute_dheefs_payload(payload)
        elif self.path == "/context/execute":
            result = _execute_context_payload(payload)
        else:
            result = _execute_router_payload(payload)
        self._send(200, result)


def _runtime_db() -> Any:
    from dhee.db.sqlite import SQLiteManager

    return SQLiteManager(str(data_dir() / "history.db"))


def _execute_dheefs_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from dhee.fs import ContextWorkspace

        repo = payload.get("repo")
        if repo:
            repo = os.path.abspath(os.path.expanduser(str(repo)))
        workspace = ContextWorkspace(
            repo=repo,
            user_id=str(payload.get("user_id") or "default"),
            agent_id=str(payload.get("agent_id") or "runtime"),
            db=_runtime_db(),
            workspace_id=payload.get("workspace_id") or repo,
        )
        result = workspace.execute(str(payload.get("command") or "")).to_dict()
        result["runtime"] = {
            "daemon": True,
            "pid": os.getpid(),
            "transport": "http-loopback",
        }
        return result
    except Exception as exc:
        return {
            "ok": False,
            "exit_code": 1,
            "command": str(payload.get("command") or ""),
            "stdout": f"{type(exc).__name__}: {exc}",
            "stderr": f"{type(exc).__name__}: {exc}",
            "data": {"error": str(exc), "error_type": type(exc).__name__},
            "runtime": {
                "daemon": True,
                "pid": os.getpid(),
                "transport": "http-loopback",
            },
        }


def _runtime_metadata() -> Dict[str, Any]:
    return {
        "daemon": True,
        "pid": os.getpid(),
        "transport": "http-loopback",
    }


def _truthy_env(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _bash_allowlist_raw() -> str:
    return str(
        os.environ.get("DHEE_RUNTIME_BASH_ALLOWLIST")
        or os.environ.get("DHEE_RUNTIME_BASH_CWD_ALLOWLIST")
        or ""
    )


def _bash_allowlist_roots() -> list[Path]:
    raw = _bash_allowlist_raw()
    roots: list[Path] = []
    for item in raw.replace(",", os.pathsep).split(os.pathsep):
        item = item.strip()
        if not item:
            continue
        try:
            root = Path(item).expanduser().resolve()
        except OSError:
            continue
        if root.is_dir():
            roots.append(root)
    return roots


def _bash_timeout_cap_seconds() -> float:
    try:
        value = float(os.environ.get("DHEE_RUNTIME_BASH_MAX_TIMEOUT") or 30.0)
    except (TypeError, ValueError):
        value = 30.0
    return max(1.0, min(value, 600.0))


def _bash_requested_timeout(arguments: Dict[str, Any]) -> float:
    try:
        value = float(arguments.get("timeout", 120.0))
    except (TypeError, ValueError):
        value = 120.0
    return max(1.0, min(value, 600.0))


def _path_within(child: Path, root: Path) -> bool:
    try:
        child.relative_to(root)
        return True
    except ValueError:
        return False


def _bash_runtime_status() -> Dict[str, Any]:
    return {
        "enabled": _truthy_env("DHEE_RUNTIME_ENABLE_BASH"),
        "allowlist": [str(path) for path in _bash_allowlist_roots()],
        "timeout_cap_seconds": _bash_timeout_cap_seconds(),
        "trust_boundary": "server_env_enable_and_cwd_allowlist",
    }


def _bash_router_error(error: str, *, action: str, **extra: Any) -> Dict[str, Any]:
    payload = {
        "format": "dhee_router_error",
        "error": error,
        "action": action,
        "runtime": _runtime_metadata(),
    }
    payload["runtime"]["bash"] = _bash_runtime_status()
    payload.update(extra)
    return payload


def _execute_context_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from dhee.context_state import ContextStateStore

        repo = payload.get("repo")
        if repo:
            repo = os.path.abspath(os.path.expanduser(str(repo)))
        store = ContextStateStore(
            repo=repo,
            workspace_id=payload.get("workspace_id") or repo,
            user_id=str(payload.get("user_id") or "default"),
            agent_id=str(payload.get("agent_id") or "runtime"),
        )
        action = str(payload.get("action") or "").strip().lower()
        if action == "status":
            result = store.status()
        elif action == "state":
            fmt = str(payload.get("format") or "card").lower()
            if fmt == "json":
                result = {"format": "dhee_context_state", "state": store.load(), "status": store.status()}
            elif fmt == "markdown":
                result = {"format": "markdown", "text": store.render_markdown()}
            else:
                result = {"format": "card", "text": store.render_state_card(), "status": store.status()}
        elif action == "debt":
            result = store.debt_summary(top=bool(payload.get("top", False)))
        elif action == "checkpoint":
            result = store.checkpoint(reason=str(payload.get("reason") or "runtime checkpoint"))
        elif action == "rollover":
            result = store.rollover(reason=str(payload.get("reason") or "runtime rollover"))
        elif action == "provision":
            result = store.provision(str(payload.get("task") or payload.get("query") or ""))
        else:
            result = {
                "format": "dhee_context_error",
                "error": "unknown_context_action",
                "action": action,
            }
        result["runtime"] = _runtime_metadata()
        return result
    except Exception as exc:
        return {
            "format": "dhee_context_error",
            "error": str(exc),
            "error_type": type(exc).__name__,
            "action": str(payload.get("action") or ""),
            "runtime": _runtime_metadata(),
        }


def _execute_bash_router_payload(arguments: Dict[str, Any], *, action: str) -> Dict[str, Any]:
    if not _truthy_env("DHEE_RUNTIME_ENABLE_BASH"):
        return _bash_router_error("bash_runtime_not_enabled", action=action)

    roots = _bash_allowlist_roots()
    if not roots:
        return _bash_router_error("bash_runtime_allowlist_empty", action=action)

    cwd_arg = arguments.get("cwd") or os.getcwd()
    cwd_path = Path(str(cwd_arg)).expanduser()
    if not cwd_path.is_absolute():
        cwd_path = Path(os.getcwd()) / cwd_path
    try:
        cwd = cwd_path.resolve()
    except OSError as exc:
        return _bash_router_error(
            "bash_runtime_cwd_unresolvable",
            action=action,
            cwd=str(cwd_path),
            detail=f"{type(exc).__name__}: {exc}",
        )
    if not cwd.is_dir():
        return _bash_router_error("bash_runtime_cwd_missing", action=action, cwd=str(cwd))

    matches = [root for root in roots if _path_within(cwd, root)]
    if not matches:
        return _bash_router_error(
            "bash_runtime_cwd_not_allowlisted",
            action=action,
            cwd=str(cwd),
            allowlist=[str(root) for root in roots],
        )

    requested_timeout = _bash_requested_timeout(arguments)
    timeout_cap = _bash_timeout_cap_seconds()
    effective_timeout = min(requested_timeout, timeout_cap)
    runtime_arguments = dict(arguments)
    runtime_arguments["cwd"] = str(cwd)
    runtime_arguments["timeout"] = effective_timeout

    from dhee.router.handlers import handle_dhee_bash

    result = handle_dhee_bash(runtime_arguments)
    result["runtime"] = _runtime_metadata()
    result["runtime"]["bash"] = {
        "enabled": True,
        "enabled_by": "DHEE_RUNTIME_ENABLE_BASH",
        "cwd": str(cwd),
        "allowlist_match": str(matches[0]),
        "allowlist": [str(root) for root in roots],
        "requested_timeout_seconds": requested_timeout,
        "effective_timeout_seconds": effective_timeout,
        "timeout_cap_seconds": timeout_cap,
        "trust_boundary": "server_env_enable_and_cwd_allowlist",
        "environment": {
            "shell": os.environ.get("SHELL") or "/bin/sh",
            "python": sys.executable,
        },
    }
    return result


def _execute_router_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from dhee.router.handlers import handle_dhee_grep, handle_dhee_read

        action = str(payload.get("action") or "").strip().lower()
        arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
        if action in {"read", "dhee_read"}:
            result = handle_dhee_read(arguments)
        elif action in {"grep", "dhee_grep"}:
            result = handle_dhee_grep(arguments)
        elif action in {"bash", "dhee_bash"}:
            return _execute_bash_router_payload(arguments, action=action)
        else:
            return {
                "format": "dhee_router_error",
                "error": "unsupported_router_action",
                "action": action,
                "runtime": _runtime_metadata(),
            }
        result["runtime"] = _runtime_metadata()
        return result
    except Exception as exc:
        return {
            "format": "dhee_router_error",
            "error": str(exc),
            "error_type": type(exc).__name__,
            "action": str(payload.get("action") or ""),
            "runtime": _runtime_metadata(),
        }
