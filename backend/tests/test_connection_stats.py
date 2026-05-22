"""Tests for get_connection_stats — the leak-detection metric (#321).

Surfaces this process's open-connection count vs the ephemeral port range
so a connection leak (2026-05-22 incident) is caught long before it
exhausts the range and wedges the gateway.
"""

from __future__ import annotations

import builtins
import io


from app.services.infra import system_monitor_service as sms


def _fake_open_factory(tcp_lines: int, port_range: str = "32768\t60999\n"):
    """Return an ``open`` replacement that feeds fake /proc files.

    /proc/net/tcp gets ``tcp_lines`` connection rows (plus a header);
    /proc/net/tcp6 is empty (header only); the port-range file controls
    the ephemeral size. Any other path delegates to the real open.
    """
    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        if path == "/proc/net/tcp":
            return io.StringIO("sl header\n" + "row\n" * tcp_lines)
        if path == "/proc/net/tcp6":
            return io.StringIO("sl header\n")
        if path == "/proc/sys/net/ipv4/ip_local_port_range":
            return io.StringIO(port_range)
        return real_open(path, *args, **kwargs)

    return fake_open


def test_conn_stats_ok(monkeypatch):
    monkeypatch.setattr(builtins, "open", _fake_open_factory(100))
    r = sms.get_connection_stats()
    assert r["open_conns"] == 100
    assert r["ephemeral_range"] == 28232  # 60999-32768+1
    assert r["status"] == "ok"


def test_conn_stats_warning(monkeypatch):
    monkeypatch.setattr(builtins, "open", _fake_open_factory(6000))
    r = sms.get_connection_stats()
    assert r["open_conns"] == 6000
    assert r["status"] == "warning"


def test_conn_stats_critical(monkeypatch):
    # 27000 / 28232 ≈ 95% ≥ 85% critical threshold
    monkeypatch.setattr(builtins, "open", _fake_open_factory(27000))
    r = sms.get_connection_stats()
    assert r["status"] == "critical"
    assert r["percent"] >= 85


def test_conn_stats_shape_on_real_proc():
    """On any platform the call returns the documented shape and a valid
    status (no /proc on macOS → 0 conns → ok; real /proc on Linux CI)."""
    r = sms.get_connection_stats()
    assert set(r) == {"open_conns", "ephemeral_range", "percent", "status"}
    assert r["status"] in {"ok", "warning", "critical", "error"}
    assert isinstance(r["open_conns"], int)
