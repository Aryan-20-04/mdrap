"""Security and Dependency Audit Script."""

import os
import re
from pathlib import Path

PATTERNS = [
    (r"api[_-]?key\s*=\s*['\"][a-zA-Z0-9_\-]{16,}['\"]", "API Key"),
    (r"secret[_-]?key\s*=\s*['\"][a-zA-Z0-9_\-]{16,}['\"]", "Secret Key"),
    (r"password\s*=\s*['\"][^'\"]{6,}['\"]", "Password"),
    (r"bearer\s+[a-zA-Z0-9_\-\.]{20,}", "Bearer Token"),
    (r"-----BEGIN\s+PRIVATE\s+KEY-----", "Private Key Header"),
]

hits = []
for root, dirs, files in os.walk("."):
    if any(x in root for x in (".git", ".pytest_cache", ".venv", ".agent", "audit", "brag-output")):
        continue
    for f in files:
        if f.endswith((".py", ".c", ".h", ".yaml", ".toml", ".json", ".sh", ".bat")):
            p = os.path.join(root, f)
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as fp:
                    content = fp.read()
                    for pat, name in PATTERNS:
                        for m in re.finditer(pat, content, re.IGNORECASE):
                            # Skip test mocks / fixtures
                            if "test" in p.lower() or "mock" in content.lower():
                                continue
                            hits.append((p, name, m.group(0)[:50]))
            except Exception:
                pass

print(f"Total production secret scan findings: {len(hits)}")
for p, name, sample in hits:
    print(f"  [{name}] {p}: {sample}")
