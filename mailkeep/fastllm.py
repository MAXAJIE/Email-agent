"""Fast, observable model access: health probe, bounded retries, event log.

Why this exists (see mailkeep-report.md, "Latency diagnosis"):

* OpenMinion retries a failing provider call internally
  (``provider_retry_max_attempts`` = 3) with the provider timeout per attempt
  (120 s, 480 s for the Cortensor Portal). Mailkeep's own turn deadline was
  120 s, so a slow or dead endpoint always surfaced as a silent timeout.
* Nothing checked whether the endpoint was reachable; every message waited
  for the full deadline even when the provider had been down for minutes.

This module adds a cheap background reachability probe (``GET /models``),
a one-shot OpenAI-compatible completion for small, tool-free jobs (writing a
draft body), and ``with_retries`` which logs every failed attempt to the
console and to ``.openminion/logs/mailkeep.log``. API keys are sent only to
the configured provider and are never logged.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .config import ROOT, setting

LOG_FILE = ROOT / ".openminion" / "logs" / "mailkeep.log"
_log_lock = threading.Lock()


def log_event(kind: str, message: str) -> None:
    """One line to stderr and to the local log file (best effort, never raises)."""
    line = f"{datetime.now().isoformat(timespec='seconds')} [{kind}] {' '.join(str(message).split())[:600]}"
    try:
        print(f"[mailkeep] {line}", file=sys.stderr, flush=True)
    except Exception:
        pass
    try:
        with _log_lock:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with LOG_FILE.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception:
        pass


def _number(name: str, default: float, low: float, high: float) -> float:
    raw = setting(name)
    try:
        value = float(raw) if raw else default
    except ValueError:
        return default
    return min(max(value, low), high)


def retry_attempts() -> int:
    """Total model attempts per turn (1 + MAILKEEP_LLM_RETRIES, default 2 retries)."""
    return 1 + int(_number("MAILKEEP_LLM_RETRIES", 2, 0, 5))


def attempt_timeout(preset_timeout: float | None = None) -> float:
    """Seconds for one model attempt. Short by default; slow portals get more."""
    default = 45.0
    if preset_timeout and preset_timeout > 120:  # Cortensor Portal preset
        default = 90.0
    return _number("MAILKEEP_LLM_ATTEMPT_TIMEOUT", default, 5, 600)


def retry_backoff() -> float:
    return _number("MAILKEEP_LLM_RETRY_BACKOFF", 0.5, 0, 10)


class RetriesExhausted(RuntimeError):
    def __init__(self, attempts: int, last: BaseException):
        super().__init__(f"failed after {attempts} attempt(s): {describe_error(last)}")
        self.attempts = attempts
        self.last = last


class NotRetryable(RuntimeError):
    """An error that another attempt cannot fix (bad key, bad request)."""


def describe_error(exc: BaseException) -> str:
    code = getattr(exc, "code", "")
    message = getattr(exc, "message", "") or str(exc) or type(exc).__name__
    return f"{type(exc).__name__}{f' ({code})' if code else ''}: {message}"[:300]


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (NotRetryable, InterruptedError, KeyboardInterrupt)):
        return False
    text = describe_error(exc).lower()
    return not any(m in text for m in ("401", "403", "unauthor", "forbidden", "invalid api key", "auth_"))


def with_retries(call: Callable[[int], object], *, what: str, attempts: int | None = None,
                 on_retry: Callable[[int, int, BaseException], None] | None = None,
                 cancelled: Callable[[], bool] = lambda: False,
                 before_retry: Callable[[], None] | None = None):
    """Run ``call(attempt)`` up to ``attempts`` times; log every failure.

    Raises RetriesExhausted when every attempt failed, or the original error
    when it is not retryable or the user cancelled.
    """
    attempts = attempts or retry_attempts()
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        if cancelled():
            raise InterruptedError("Stopped")
        try:
            return call(attempt)
        except BaseException as exc:  # noqa: BLE001 - classified below
            last = exc
            if isinstance(exc, (InterruptedError, KeyboardInterrupt)) or cancelled():
                raise
            log_event("llm-fail", f"{what}: attempt {attempt}/{attempts} failed - {describe_error(exc)}")
            if not is_retryable(exc):
                log_event("llm-fail", f"{what}: not retryable, giving up")
                raise
            if attempt < attempts:
                if on_retry:
                    on_retry(attempt, attempts, exc)
                if before_retry:
                    try:
                        before_retry()
                    except Exception:
                        pass
                time.sleep(retry_backoff() * attempt)
    log_event("llm-giveup", f"{what}: giving up after {attempts} attempt(s)")
    raise RetriesExhausted(attempts, last or RuntimeError("unknown"))


def _endpoint(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def _headers(preset) -> dict:
    from . import llm

    key = llm._api_key(preset)
    headers = {"Content-Type": "application/json", "User-Agent": "mailkeep"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if preset.slot == "openrouter":
        headers["X-Title"] = "mailkeep"
    return headers


@dataclass
class Health:
    state: str = "unknown"  # unknown | online | offline | auth_error | not_configured
    latency_ms: int = 0
    detail: str = ""
    checked_at: float = 0.0
    failures: int = 0

    def public(self) -> dict:
        age = int(time.time() - self.checked_at) if self.checked_at else None
        return {"state": self.state, "latency_ms": self.latency_ms, "detail": self.detail,
                "checked_seconds_ago": age, "consecutive_failures": self.failures}


def probe(preset, timeout: float = 6.0) -> Health:
    """One reachability check. Any HTTP answer below 500 means "reachable"."""
    if preset is None:
        return Health("not_configured", detail="no model provider configured", checked_at=time.time())
    started = time.monotonic()
    request = urllib.request.Request(_endpoint(preset.base_url, "models"), headers=_headers(preset))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(2048)
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except Exception as exc:  # DNS, refused, TLS, timeout
        return Health("offline", detail=describe_error(exc), checked_at=time.time())
    latency = int((time.monotonic() - started) * 1000)
    if status in (401, 403):
        return Health("auth_error", latency, f"HTTP {status}: the API key was rejected", time.time())
    if status >= 500:
        return Health("offline", latency, f"HTTP {status} from provider", time.time())
    return Health("online", latency, f"HTTP {status}", time.time())


class HealthMonitor:
    """Background probe every MAILKEEP_HEALTH_INTERVAL seconds (default 20)."""

    def __init__(self, preset_getter: Callable[[], object], prober: Callable | None = None):
        self._get = preset_getter
        self._probe = prober or (lambda preset: probe(preset))
        self._lock = threading.Lock()
        self.health = Health()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.interval = _number("MAILKEEP_HEALTH_INTERVAL", 20, 3, 600)

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="mailkeep-health", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.check_now()
            # Probe faster while the provider is down so recovery is noticed quickly.
            wait = self.interval if self.health.state == "online" else max(3.0, self.interval / 3)
            self._stop.wait(wait)

    def check_now(self) -> Health:
        try:
            preset = self._get()
        except Exception as exc:
            result = Health("not_configured", detail=str(exc)[:200], checked_at=time.time())
        else:
            result = self._probe(preset)
        with self._lock:
            previous = self.health.state
            result.failures = 0 if result.state == "online" else self.health.failures + 1
            self.health = result
        if result.state != previous:
            log_event("llm-health", f"{previous} -> {result.state} ({result.detail})")
        return result

    def snapshot(self) -> Health:
        with self._lock:
            return self.health

    def usable(self, max_age: float = 30.0) -> tuple[bool, Health]:
        """Fresh verdict for "should we even try the model now?".

        Re-probes when the last answer is stale or negative, so a provider
        that came back is used again on the very next user message.
        """
        current = self.snapshot()
        if current.state != "online" or time.time() - current.checked_at > max_age:
            current = self.check_now()
        return current.state in ("online", "unknown"), current


def complete(preset, system: str, user: str, *, max_tokens: int = 500,
             timeout: float = 30.0, cancel: threading.Event | None = None) -> str:
    """Single chat completion (no tools) against an OpenAI-compatible endpoint."""
    if preset is None:
        raise NotRetryable("no model provider configured")
    body = {
        "model": preset.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    effort = setting("MAILKEEP_LLM_REASONING_EFFORT").strip().lower()
    if effort in ("low", "medium", "high", "minimal", "none"):
        body["reasoning_effort"] = effort
    request = urllib.request.Request(
        _endpoint(preset.base_url, "chat/completions"),
        data=json.dumps(body).encode(), headers=_headers(preset), method="POST")
    result: dict = {}

    def run():
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result["data"] = json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read(300).decode("utf-8", "replace") if exc.fp else ""
            message = f"HTTP {exc.code}: {detail}"
            result["error"] = NotRetryable(message) if exc.code in (400, 401, 403, 404) else RuntimeError(message)
        except BaseException as exc:  # noqa: BLE001
            result["error"] = exc

    worker = threading.Thread(target=run, name="mailkeep-complete", daemon=True)
    worker.start()
    deadline = time.monotonic() + timeout + 1
    while worker.is_alive():
        if cancel is not None and cancel.is_set():
            raise InterruptedError("Stopped")
        if time.monotonic() > deadline:
            raise TimeoutError(f"no answer within {int(timeout)} s")
        worker.join(0.1)
    if "error" in result:
        raise result["error"]
    try:
        message = result["data"]["choices"][0]["message"]
        text = (message.get("content") or "").strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        raise RuntimeError("provider returned an unexpected response shape") from None
    if not text:
        raise RuntimeError("provider returned an empty answer")
    return text
