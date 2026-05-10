"""Skill content security scanner.

Background
==========
Skill markdown files (in ``skill_files``) get loaded by SeedLoader at
startup AND can be uploaded by users via PUT /skills/{slug}/files/{path}.
Their *body* is fed to the LLM as a tool prompt — but a skill can also
contain *scripts* and *references* sub-files. Those sub-files are
read by the agent's runtime, not just the LLM. A malicious skill
could therefore:

  - Embed Python code that executes os.system / subprocess in a
    references/ file the LLM is encouraged to "exec"
  - Harvest env vars (OPENAI_API_KEY etc.) and exfil via a network call
  - Read /etc/passwd or other host files
  - Use base64-decoded payloads to obscure the above patterns

This scanner runs against every uploaded body and flags suspicious
patterns. The first cut is **non-blocking** — findings are returned to
the caller as warnings; UI surfaces them but the upload still succeeds.
A future CRITICAL escalation could 422-reject high-severity findings.

Adapted from openclaw/security/skill-scanner.ts. Two layers:

  1. Line scan: per-line regex against narrow patterns. Cheap, runs
     on every line. Catches direct dangerous calls.
  2. Source scan: full-document patterns for multiline obfuscation —
     base64+exec, hex strings paired with eval, very long unbroken
     identifier-looking strings.

Output is a list of Finding(line, category, severity, snippet, message).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Sequence


class Severity(str, Enum):
    INFO = "info"  # mention; rarely actionable on its own
    WARN = "warn"  # show in UI; user should review
    HIGH = "high"  # show prominently; consider blocking


class Category(str, Enum):
    DANGEROUS_EXEC = "dangerous_exec"
    ENV_HARVESTING = "env_harvesting"
    FS_ESCAPE = "fs_escape"
    NETWORK_EXFIL = "network_exfil"
    OBFUSCATION = "obfuscation"


@dataclass(frozen=True)
class Finding:
    line: int  # 1-indexed; 0 for source-level findings
    category: Category
    severity: Severity
    snippet: str  # ≤ 120 chars from the matched region
    message: str  # human-readable explanation


# ─── Per-line patterns ──────────────────────────────────────────────
# Each tuple is (compiled_regex, category, severity, message).
# Patterns must be NARROW — false positives ruin the signal.
_LINE_PATTERNS: List[tuple[re.Pattern[str], Category, Severity, str]] = [
    # dangerous_exec
    (
        re.compile(r"\bos\.system\s*\("),
        Category.DANGEROUS_EXEC,
        Severity.HIGH,
        "os.system invokes a shell — prefer subprocess with args list",
    ),
    (
        re.compile(
            r"\bsubprocess\.(?:run|call|Popen|check_output|check_call)\s*\(.*shell\s*=\s*True"
        ),
        Category.DANGEROUS_EXEC,
        Severity.HIGH,
        "subprocess with shell=True allows command injection",
    ),
    (
        re.compile(r"\bexec\s*\("),
        Category.DANGEROUS_EXEC,
        Severity.HIGH,
        "exec() runs arbitrary code at runtime",
    ),
    (
        re.compile(r"(?<!\w)eval\s*\("),
        Category.DANGEROUS_EXEC,
        Severity.HIGH,
        "eval() runs arbitrary expression at runtime",
    ),
    (
        re.compile(r"\b__import__\s*\(\s*['\"](?:os|subprocess|sys)['\"]"),
        Category.DANGEROUS_EXEC,
        Severity.HIGH,
        "dynamic __import__ of os/subprocess/sys is a sandbox escape pattern",
    ),
    (
        re.compile(r"\bpickle\.(?:load|loads)\s*\("),
        Category.DANGEROUS_EXEC,
        Severity.HIGH,
        "pickle load can execute arbitrary code from untrusted bytes",
    ),
    (
        re.compile(r"\bcompile\s*\([^)]+\)\s*[,;)]?\s*\n?\s*(?:exec|eval)\s*\("),
        Category.DANGEROUS_EXEC,
        Severity.HIGH,
        "compile() chained into exec/eval is a known sandbox-bypass pattern",
    ),
    # env_harvesting
    (
        re.compile(
            r"\bos\.environ(?:\.get)?\s*[\[(]\s*['\"](?:[A-Z_]*(?:KEY|TOKEN|SECRET|PASSWORD|API)[A-Z_]*)['\"]"
        ),
        Category.ENV_HARVESTING,
        Severity.HIGH,
        "reads a secret-named env var; verify legitimate use",
    ),
    (
        re.compile(
            r"\bos\.getenv\s*\(\s*['\"](?:[A-Z_]*(?:KEY|TOKEN|SECRET|PASSWORD|API)[A-Z_]*)['\"]"
        ),
        Category.ENV_HARVESTING,
        Severity.HIGH,
        "reads a secret-named env var; verify legitimate use",
    ),
    (
        re.compile(r"\bos\.environ\s*$|\bdict\s*\(\s*os\.environ\s*\)"),
        Category.ENV_HARVESTING,
        Severity.WARN,
        "snapshotting the full env dict is a common exfil pattern",
    ),
    # fs_escape — absolute reads outside any user-scope dir
    (
        re.compile(r"open\s*\(\s*['\"]/(?:etc|root|var/log|proc|sys|home|Users)/"),
        Category.FS_ESCAPE,
        Severity.HIGH,
        "reads absolute system path outside user-scoped storage",
    ),
    (
        re.compile(r"Path\s*\(\s*['\"]/(?:etc|root|var/log|proc|sys|home|Users)/"),
        Category.FS_ESCAPE,
        Severity.HIGH,
        "Path() targets an absolute system path outside user scope",
    ),
    (
        re.compile(r"shutil\.(?:rmtree|copy|move)\s*\(\s*['\"]/"),
        Category.FS_ESCAPE,
        Severity.WARN,
        "shutil operating on absolute system path",
    ),
    # network_exfil — direct sockets / unconventional posts
    (
        re.compile(r"\bsocket\.create_connection\s*\("),
        Category.NETWORK_EXFIL,
        Severity.WARN,
        "raw socket connect bypasses the safe_http boundary",
    ),
    (
        re.compile(r"urllib\.(?:request|urlopen)\b"),
        Category.NETWORK_EXFIL,
        Severity.WARN,
        "urllib bypasses safe_async_client / SSRF guards",
    ),
]


# ─── Source-level (multi-line / obfuscation) patterns ──────────────
_OBFUSCATION_BASE64_EXEC = re.compile(
    r"(?:base64\.b64decode|codecs\.decode\s*\([^)]*['\"]base64['\"])"
    r".*?(?:exec|eval)\s*\(",
    re.DOTALL,
)
_OBFUSCATION_HEX_EXEC = re.compile(
    r"bytes\.fromhex\s*\([^)]+\)\s*(?:\.decode\(\)\s*)?\.?(?:run\(|exec\(|eval\()",
    re.DOTALL,
)
# Long unbroken base64-looking string (≥ 200 chars of base64 alphabet,
# no whitespace) suggests embedded payload. False-positive prone, so
# WARN only.
_LONG_B64_BLOB = re.compile(r"['\"]([A-Za-z0-9+/=]{200,})['\"]")


def _snippet(text: str, max_len: int = 120) -> str:
    """Strip newlines and clip to max_len for display."""
    s = text.replace("\n", " ").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def scan(content: str) -> List[Finding]:
    """Scan a body of text for dangerous patterns. Returns Finding[]
    sorted by severity descending then line number ascending."""
    findings: List[Finding] = []

    # Layer 1: per-line scan
    for line_no, line in enumerate(content.splitlines(), start=1):
        for pattern, category, severity, message in _LINE_PATTERNS:
            m = pattern.search(line)
            if m:
                findings.append(
                    Finding(
                        line=line_no,
                        category=category,
                        severity=severity,
                        snippet=_snippet(line),
                        message=message,
                    )
                )

    # Layer 2: full-source obfuscation patterns
    if _OBFUSCATION_BASE64_EXEC.search(content):
        # Find the line of the first occurrence for context
        m = _OBFUSCATION_BASE64_EXEC.search(content)
        approx_line = content[: m.start()].count("\n") + 1 if m else 0
        findings.append(
            Finding(
                line=approx_line,
                category=Category.OBFUSCATION,
                severity=Severity.HIGH,
                snippet=_snippet(content[max(0, m.start()) : m.end()]),
                message="base64 decode chained into exec/eval — classic obfuscated payload",
            )
        )

    if _OBFUSCATION_HEX_EXEC.search(content):
        m = _OBFUSCATION_HEX_EXEC.search(content)
        approx_line = content[: m.start()].count("\n") + 1 if m else 0
        findings.append(
            Finding(
                line=approx_line,
                category=Category.OBFUSCATION,
                severity=Severity.HIGH,
                snippet=_snippet(content[max(0, m.start()) : m.end()]),
                message="hex-decoded string flows into exec/eval — likely obfuscation",
            )
        )

    for m in _LONG_B64_BLOB.finditer(content):
        approx_line = content[: m.start()].count("\n") + 1
        findings.append(
            Finding(
                line=approx_line,
                category=Category.OBFUSCATION,
                severity=Severity.WARN,
                snippet=_snippet(m.group(1), max_len=80),
                message="long base64-looking blob (≥200 chars) — verify intent",
            )
        )
        # Avoid spamming on a file with many blobs; cap to 3 reports
        if (
            sum(
                1
                for f in findings
                if f.category == Category.OBFUSCATION
                and f.message.startswith("long base64")
            )
            >= 3
        ):
            break

    # Stable sort: HIGH first, then WARN, then INFO; line number tiebreak
    severity_rank = {Severity.HIGH: 0, Severity.WARN: 1, Severity.INFO: 2}
    findings.sort(key=lambda f: (severity_rank[f.severity], f.line))
    return findings


def has_blocking_findings(findings: Sequence[Finding]) -> bool:
    """Caller asks: does this scan justify rejecting the upload?
    Currently: any HIGH severity blocks. Tunable per deployment if a
    reject toggle is added."""
    return any(f.severity == Severity.HIGH for f in findings)


def to_dict_list(findings: Sequence[Finding]) -> List[dict]:
    """Serialize for API response."""
    return [
        {
            "line": f.line,
            "category": f.category.value,
            "severity": f.severity.value,
            "snippet": f.snippet,
            "message": f.message,
        }
        for f in findings
    ]


__all__ = [
    "Severity",
    "Category",
    "Finding",
    "scan",
    "has_blocking_findings",
    "to_dict_list",
]
