"""
health.py
In-process metrics collector + lightweight HTTP server.

Exposes two endpoints:
  GET /health   — liveness check (returns 200 OK or 503 if unhealthy)
  GET /metrics  — JSON snapshot of all counters and state

Run as a daemon thread alongside the main polling loop.
No external dependencies — uses only Python stdlib.

Ping /health from your monitoring system (Uptime Kuma, Nagios,
a simple cron job, or Docker's HEALTHCHECK) to know the app is alive.
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

log = logging.getLogger(__name__)


class Metrics:
    """
    Thread-safe counters. A single instance is shared between the
    polling loop (writes) and the HTTP server (reads).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {
            "started_at":           datetime.now(timezone.utc).isoformat(),
            "emails_processed":     0,
            "emails_routed":        0,
            "emails_fallback":      0,
            "emails_failed":        0,
            "poll_cycles":          0,
            "poll_errors":          0,
            "consecutive_errors":   0,
            "last_poll_at":         None,
            "last_success_at":      None,
            "last_error_at":        None,
            "last_error_message":   None,
            "fallback_queue_depth": 0,
            "category_counts":      {},
        }

    def _inc(self, key: str, amount: int = 1) -> None:
        with self._lock:
            self._data[key] = self._data.get(key, 0) + amount

    def _set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value

    def snapshot(self) -> dict:
        with self._lock:
            snap = dict(self._data)
        snap["uptime_seconds"] = int(
            (datetime.now(timezone.utc) -
             datetime.fromisoformat(snap["started_at"])).total_seconds()
        )
        return snap

    # ── Counters called from main.py / router.py ──────────────────────────────

    def record_poll(self) -> None:
        self._inc("poll_cycles")
        self._set("last_poll_at", datetime.now(timezone.utc).isoformat())

    def record_poll_error(self, message: str) -> None:
        self._inc("poll_errors")
        self._inc("consecutive_errors")
        self._set("last_error_at",      datetime.now(timezone.utc).isoformat())
        self._set("last_error_message", message[:200])

    def record_poll_success(self) -> None:
        self._set("consecutive_errors", 0)
        self._set("last_success_at",    datetime.now(timezone.utc).isoformat())

    def record_routed(self, category: str) -> None:
        self._inc("emails_processed")
        self._inc("emails_routed")
        with self._lock:
            cats = self._data["category_counts"]
            cats[category] = cats.get(category, 0) + 1

    def record_fallback(self) -> None:
        self._inc("emails_processed")
        self._inc("emails_fallback")

    def record_failed(self) -> None:
        self._inc("emails_processed")
        self._inc("emails_failed")

    def set_fallback_queue_depth(self, depth: int) -> None:
        self._set("fallback_queue_depth", depth)

    def is_healthy(self) -> tuple[bool, str]:
        """
        Returns (healthy: bool, reason: str).
        Unhealthy conditions:
          - 5+ consecutive poll errors
          - No successful poll in the last 10 minutes (after warmup)
        """
        snap = self.snapshot()

        if snap["consecutive_errors"] >= 5:
            return False, f"{snap['consecutive_errors']} consecutive poll errors"

        last_success = snap.get("last_success_at")
        uptime       = snap["uptime_seconds"]
        if last_success and uptime > 120:
            last_success_dt = datetime.fromisoformat(last_success)
            seconds_since   = (datetime.now(timezone.utc) - last_success_dt).total_seconds()
            if seconds_since > 600:
                return False, f"No successful poll in {int(seconds_since)}s"

        return True, "ok"


# ── Singleton accessed from anywhere in the app ───────────────────────────────
metrics = Metrics()


# ── HTTP server ───────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):

    def do_GET(self) -> None:
        # Strip query string before matching — /health?foo=bar should still work
        path = self.path.split("?")[0].split("#")[0]
        if path == "/health":
            self._health()
        elif path == "/metrics":
            self._metrics()
        else:
            self._respond(404, {"error": "not found"})

    def _health(self) -> None:
        healthy, reason = metrics.is_healthy()
        status = 200 if healthy else 503
        self._respond(status, {"status": "ok" if healthy else "unhealthy", "reason": reason})

    def _metrics(self) -> None:
        self._respond(200, metrics.snapshot())

    def _respond(self, status: int, body: dict) -> None:
        payload = json.dumps(body, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args) -> None:  # silence default access logs
        pass


def start_health_server(port: int = 8080, host: str = "127.0.0.1") -> None:
    """
    Start the health/metrics HTTP server in a daemon thread.

    Binds to 127.0.0.1 (localhost only) by default.
    The metrics endpoint exposes internal operational data — email counts,
    error messages, sender patterns — that should not be visible to the
    whole network. Only expose to 0.0.0.0 if your monitoring system is
    on a separate host AND the port is firewalled from untrusted networks.

    Override via HEALTH_HOST env var in docker-compose.yml if needed.
    """
    server = HTTPServer((host, port), _Handler)

    def _serve():
        log.info("Health server listening on http://%s:%d", host, port)
        server.serve_forever()

    thread = threading.Thread(target=_serve, daemon=True, name="health-server")
    thread.start()
