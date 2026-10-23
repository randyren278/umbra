"""Persistent, explicit-profile Umbra client. Standard library only."""
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import uuid


class UmbraError(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code, self.details = code, details or {"code": code, "message": message}


def profile_socket(profile):
    if not isinstance(profile, str) or not profile.strip() or "\0" in profile:
        raise ValueError("an explicit nonempty profile is required")
    safe = profile.replace("/", "_")
    suffix = safe
    if len(safe) > 24:
        digest = hashlib.sha256(safe.encode()).hexdigest()
        suffix = digest[:8] + "-" + digest[8:11]
    return Path.home() / "Library/Application Support/Umbra" / f"daemon-{suffix}.sock"


class Umbra:
    def __init__(self, profile, *, binary=None, socket_path=None, max_reply_bytes=16 * 1024 * 1024):
        path = profile_socket(profile)
        if not isinstance(max_reply_bytes, int) or isinstance(max_reply_bytes, bool) or max_reply_bytes < 1:
            raise ValueError("max_reply_bytes must be positive")
        self.profile, self.binary = profile, binary
        self.socket_path = str(socket_path or path)
        self.max_reply_bytes = max_reply_bytes
        self._socket, self._buffer, self._closed = None, bytearray(), False
        self._lock = threading.Lock()

    def start(self, *, deadline=None):
        """Explicitly start the configured daemon; close() never stops it."""
        if not self.binary or not Path(self.binary).is_absolute():
            raise ValueError("start requires an explicit absolute Umbra binary path")
        deadline = time.time() + 20 if deadline is None else deadline
        try:
            self.call("status", deadline=deadline)
            return None
        except UmbraError as error:
            if error.code != 1 or self._closed:
                raise
        process = subprocess.Popen([self.binary, "serve"], env={**os.environ, "UMBRA_PROFILE": self.profile},
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        while time.time() < deadline:
            try:
                self.call("status", deadline=deadline)
                return process
            except UmbraError as error:
                if error.code != 1:
                    raise
                time.sleep(min(.02, max(0, deadline - time.time())))
        raise UmbraError(2, "daemon startup deadline elapsed")

    def close(self):
        self._closed = True
        self._disconnect()

    def _disconnect(self):
        current, self._socket = self._socket, None
        if current:
            try:
                current.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            current.close()
        self._buffer.clear()

    def call(self, command, args=None, *, deadline=None, request_id=None):
        """deadline is an absolute Unix timestamp in seconds, covering queue + IO."""
        deadline = time.time() + 30 if deadline is None else deadline
        if not math.isfinite(deadline):
            raise ValueError("deadline must be finite")
        if not self._lock.acquire(timeout=max(0, deadline - time.time())):
            raise UmbraError(2, "request deadline elapsed while queued")
        try:
            if self._closed:
                raise UmbraError(1, "client is closed")
            def remaining():
                value = deadline - time.time()
                if value <= 0:
                    raise TimeoutError("request deadline elapsed")
                return value
            current = self._socket
            if current is None:
                current = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._socket = current
                current.settimeout(remaining())
                current.connect(self.socket_path)
            request_id = request_id or str(uuid.uuid4())
            payload = {"id": request_id, "command": command, "args": {**(args or {}), "request": (args or {}).get("request", request_id), "deadline": deadline}}
            current.settimeout(remaining())
            current.sendall((json.dumps(payload, allow_nan=False) + "\n").encode())
            while b"\n" not in self._buffer:
                current.settimeout(remaining())
                chunk = current.recv(min(65536, self.max_reply_bytes + 1 - len(self._buffer)))
                if not chunk:
                    raise OSError("daemon closed the connection")
                self._buffer.extend(chunk)
                if len(self._buffer) > self.max_reply_bytes:
                    raise UmbraError(1, "reply exceeds max_reply_bytes")
            line, _, rest = self._buffer.partition(b"\n")
            self._buffer[:] = rest
            reply = json.loads(line)
            if not isinstance(reply, dict) or reply.get("id") != request_id or not isinstance(reply.get("ok"), bool):
                raise ValueError("malformed or mismatched reply")
            if not reply["ok"]:
                error = reply.get("error")
                if not isinstance(error, dict) or not isinstance(error.get("code"), (int, str)) or not isinstance(error.get("message"), str):
                    raise ValueError("malformed error reply")
                raise UmbraError(error.get("code", 1), error.get("message", "request failed"), error)
            result = reply.get("result")
            if not isinstance(result, dict):
                raise ValueError("malformed result reply")
            return result
        except (TimeoutError, socket.timeout) as error:
            self._disconnect()
            raise UmbraError(2, str(error)) from error
        except (OSError, ValueError) as error:
            self._disconnect()
            raise UmbraError(1, str(error)) from error
        except UmbraError:
            # A server error keeps framing valid; oversized replies do not.
            if len(self._buffer) > self.max_reply_bytes:
                self._disconnect()
            raise
        finally:
            self._lock.release()

    def propose_repair(self, step, callback, *, observe_args=None, deadline=None):
        """Observe and ask an optional provider for new args. Never dispatch the step."""
        if not isinstance(step, dict) or not isinstance(step.get("command"), str) or not step["command"].strip():
            raise ValueError("step requires a command")
        if not isinstance(step.get("args", {}), dict):
            raise ValueError("step args must be an object")
        deadline = time.time() + 30 if deadline is None else deadline
        observation = self.call("observe", observe_args, deadline=deadline)
        replacement = callback(observation, json.loads(json.dumps(step, allow_nan=False)))
        if not isinstance(replacement, dict):
            raise ValueError("repair callback must return an args object")
        json.dumps(replacement, allow_nan=False)
        if time.time() >= deadline:
            raise UmbraError(2, "repair proposal deadline elapsed")
        return {"step": {**step, "args": replacement}, "observation": observation, "dispatched": False}

    def cancel(self, request_id, *, agent=None, task=None, deadline=None):
        control = Umbra(self.profile, socket_path=self.socket_path, max_reply_bytes=self.max_reply_bytes)
        try:
            return control.call("cancel", {"id": request_id, **({"agent": agent} if agent is not None else {}), **({"task": task} if task is not None else {})}, deadline=deadline)
        finally:
            control.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
