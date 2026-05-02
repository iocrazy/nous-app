"""End-to-end smoke for the boundary layer.

Exercises the real reject paths against a live SsrfProxy + SafeAsyncClient
without standing up the full FastAPI app. Run from backend/ with:

    uv run python scripts/smoke_boundary.py

Prints PASS/FAIL per check + final summary. Returns non-zero exit code
if any check failed (so CI / make targets can gate on it).
"""
from __future__ import annotations

import asyncio
import socket
import sys
import time

# Ensure backend is on sys.path
sys.path.insert(0, ".")


async def _probe_ports() -> dict[str, bool]:
    """Quick liveness for boundary-relevant local services.

    Boundary smoke does not need a running mediahub backend — we exercise
    the modules directly. This block is informational only."""
    return {}


async def main() -> int:
    failures: list[str] = []

    def report(label: str, ok: bool, detail: str = "") -> None:
        mark = "✅ PASS" if ok else "❌ FAIL"
        suffix = f" — {detail}" if detail else ""
        print(f"  {mark}  {label}{suffix}")
        if not ok:
            failures.append(label)

    print("=" * 70)
    print("Boundary layer end-to-end smoke")
    print("=" * 70)

    # ------------------------------------------------------------------
    # Layer 1 — validate_url_async direct adversarial inputs
    # ------------------------------------------------------------------
    print("\n[L1] validate_url_async — adversarial inputs:")
    from app.boundary import URLBlockedError, validate_url_async

    cases = [
        ("NAS Supabase literal",
         "http://192.168.50.9:9080/admin"),
        ("Decimal IPv4 bypass (==127.0.0.1)",
         "http://2130706433/admin"),
        ("Octal IPv4 bypass (0177.0.0.1==127.0.0.1)",
         "http://0177.0.0.1/admin"),
        ("Hex IPv4 bypass (==127.0.0.1)",
         "http://0x7f000001/admin"),
        ("mDNS suffix .localhost",
         "http://printer.localhost/api"),
        (".internal suffix",
         "http://api.internal/admin"),
        ("AWS IMDS",
         "http://169.254.169.254/latest/meta-data/"),
        ("GCP metadata.google.internal",
         "http://metadata.google.internal/"),
        ("IPv6 loopback",
         "http://[::1]/"),
        ("file:// scheme",
         "file:///etc/passwd"),
        ("javascript: scheme",
         "javascript:alert(1)"),
    ]
    for label, url in cases:
        try:
            await validate_url_async(url)
            report(f"L1 reject: {label}", False, f"PASSED THROUGH (bug!) — {url}")
        except URLBlockedError:
            report(f"L1 reject: {label}", True)
        except Exception as e:
            report(f"L1 reject: {label}", False,
                   f"unexpected {type(e).__name__}: {e}")

    # ------------------------------------------------------------------
    # Layer 3 in-process — SafeAsyncClient redirect-to-private
    # ------------------------------------------------------------------
    print("\n[L3 in-process] SafeAsyncClient initial-URL block:")
    from app.boundary import safe_async_client

    async with safe_async_client() as client:
        try:
            await client.get("http://192.168.50.9:9080/admin")
            report("L3 in-process: NAS Supabase initial-URL block",
                   False, "PASSED THROUGH (bug!)")
        except URLBlockedError:
            report("L3 in-process: NAS Supabase initial-URL block", True)
        except Exception as e:
            report("L3 in-process: NAS Supabase initial-URL block",
                   False, f"unexpected {type(e).__name__}: {e}")

    # ------------------------------------------------------------------
    # Layer 3 cross-process — SsrfProxy real server
    # ------------------------------------------------------------------
    print("\n[L3 proxy] SsrfProxy live server:")
    from app.boundary import SsrfProxy

    proxy = SsrfProxy()
    await proxy.start()
    try:
        # Smoke 1: CONNECT to literal private IP
        async def send_connect(target: str) -> int:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", proxy.port
            )
            writer.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
            await writer.drain()
            response = await asyncio.wait_for(reader.read(4096), timeout=2.0)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            status_line = response.split(b"\r\n", 1)[0].decode("ascii", "replace")
            try:
                return int(status_line.split(" ", 2)[1])
            except (IndexError, ValueError):
                return 0

        status = await send_connect("192.168.50.9:9080")
        report(
            f"L3 proxy: CONNECT 192.168.50.9:9080 → {status}",
            status == 403,
            f"expected 403, got {status}",
        )

        # Smoke 2: HTTP GET via proxy (absolute-form) to private IP
        async def send_http_get(absolute_url: str) -> int:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", proxy.port
            )
            writer.write(
                (
                    f"GET {absolute_url} HTTP/1.1\r\n"
                    f"Host: {absolute_url.split('/')[2]}\r\n\r\n"
                ).encode()
            )
            await writer.drain()
            response = await asyncio.wait_for(reader.read(4096), timeout=2.0)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            status_line = response.split(b"\r\n", 1)[0].decode("ascii", "replace")
            try:
                return int(status_line.split(" ", 2)[1])
            except (IndexError, ValueError):
                return 0

        status = await send_http_get("http://192.168.50.9:9080/admin")
        report(
            f"L3 proxy: HTTP GET http://192.168.50.9:9080/admin → {status}",
            status == 403,
            f"expected 403, got {status}",
        )

        status = await send_connect("metadata.google.internal:80")
        report(
            f"L3 proxy: CONNECT metadata.google.internal:80 → {status}",
            status == 403,
            f"expected 403, got {status}",
        )

        # Verify proxy URL string is valid
        report(
            f"L3 proxy: bound on {proxy.url}",
            proxy.port is not None and proxy.url.startswith("http://127.0.0.1:"),
        )
    finally:
        await proxy.stop()

    # ------------------------------------------------------------------
    # Layer 5 — audit table accepting our writes
    # ------------------------------------------------------------------
    print("\n[L5] boundary_audit DB write smoke:")
    try:
        from app.boundary import audit
        from app.db import get_async_supabase_admin

        # Insert a synthetic test row directly so we know the table works
        sb = await get_async_supabase_admin()
        marker = f"smoke-test-{int(time.time())}"
        await sb.table("boundary_audit").insert(
            {
                "layer": "l1_validate",
                "reason": "smoke_test_marker",
                "raw_url": marker,
                "metadata_json": {"smoke": True},
            }
        ).execute()
        # Read back
        result = await (
            sb.table("boundary_audit")
            .select("raw_url, reason")
            .eq("raw_url", marker)
            .limit(1)
            .execute()
        )
        report(
            "L5: boundary_audit insert + readback",
            bool(result.data) and result.data[0]["raw_url"] == marker,
            f"got {result.data}",
        )
        # Cleanup
        await sb.table("boundary_audit").delete().eq("raw_url", marker).execute()
    except Exception as e:
        report(
            "L5: boundary_audit insert + readback",
            False,
            f"{type(e).__name__}: {e}",
        )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    if failures:
        print(f"❌ SMOKE FAILED — {len(failures)} check(s) failed:")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("✅ SMOKE PASSED — all checks green")
    return 0


if __name__ == "__main__":
    code = asyncio.run(main())
    sys.exit(code)
