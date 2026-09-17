"""MDRAP Reason Generator and Synchronizer (Single Source of Truth).

Reads `src/rules.def` and ensures `src/models.py` and `src/fastpath.c` remain in
perfect synchronization with the canonical X-macro definitions.
"""
import argparse
import os
import re
import sys

RULE_REGEX = re.compile(
    r'RULE_DEF\s*\(\s*([A-Za-z0-9_]+)\s*,\s*([0-9]+)\s*,\s*"([^"]*)"\s*\)'
)

CORE_BIT_MAX = 15
RESERVED_BIT_MAX = 31
USER_BIT_MAX = 63


def parse_rules_def(rules_path: str):
    if not os.path.exists(rules_path):
        raise FileNotFoundError(f"Missing rules definition file: {rules_path}")

    rules = []
    seen_names = set()
    seen_bits = set()

    with open(rules_path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("/*") or line.startswith("*") or line.startswith("//"):
                continue
            m = RULE_REGEX.search(line)
            if m:
                name, bit_str, desc = m.group(1), m.group(2), m.group(3)
                bit = int(bit_str)

                if name in seen_names:
                    raise ValueError(f"Duplicate rule name '{name}' at line {line_no}")
                if bit in seen_bits:
                    raise ValueError(f"Duplicate rule bit '{bit}' for '{name}' at line {line_no}")

                if bit < 0 or bit > USER_BIT_MAX:
                    raise ValueError(f"Bit {bit} out of range [0, 63] for '{name}'")

                seen_names.add(name)
                seen_bits.add(bit)
                rules.append((name, bit, desc))

    rules.sort(key=lambda x: x[1])
    return rules


def check_sync(rules, repo_root: str) -> list[str]:
    errors = []
    models_path = os.path.join(repo_root, "src", "models.py")
    fastpath_c_path = os.path.join(repo_root, "src", "fastpath.c")

    # 1. Check models.py
    if not os.path.exists(models_path):
        errors.append(f"Missing {models_path}")
    else:
        with open(models_path, "r", encoding="utf-8") as f:
            content = f.read()

        match = re.search(r"class Reason\(str,\s*Enum\):\s*(?:\"\"\"[^\"]*\"\"\"\s*)?([\s\S]*?)(?=\n\nclass|\Z)", content)
        if not match:
            errors.append("Could not locate `class Reason(str, Enum):` in models.py")
        else:
            enum_block = match.group(1)
            for name, bit, desc in rules:
                expected_assign = f"{name} ="
                if expected_assign not in enum_block:
                    errors.append(f"Rule '{name}' defined in rules.def is missing from models.py Reason enum")

    # 2. Check fastpath.c includes rules.def
    if not os.path.exists(fastpath_c_path):
        errors.append(f"Missing {fastpath_c_path}")
    else:
        with open(fastpath_c_path, "r", encoding="utf-8") as f:
            c_content = f.read()
        if '#include "rules.def"' not in c_content and '#include \"rules.def\"' not in c_content:
            errors.append("src/fastpath.c does not #include \"rules.def\"")

    return errors


def sync_models(rules, repo_root: str):
    models_path = os.path.join(repo_root, "src", "models.py")
    with open(models_path, "r", encoding="utf-8") as f:
        content = f.read()

    lines = ["class Reason(str, Enum):", '    """Deterministic failure and anomaly reason codes."""', ""]
    for name, bit, desc in rules:
        lines.append(f'    {name} = "{name}"  # Bit {bit}: {desc}')

    pattern = r"class Reason\(str,\s*Enum\):[\s\S]*?(?=\n\nclass|\Z)"
    new_enum = "\n".join(lines)
    new_content = re.sub(pattern, new_enum, content)

    with open(models_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    print(f"Updated {models_path} with {len(rules)} reasons.")


def main():
    parser = argparse.ArgumentParser(description="MDRAP Rule Generator & Checker")
    parser.add_argument("--check", action="store_true", help="Check for drift without modifying files")
    args = parser.parse_args()

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    rules_def_path = os.path.join(repo_root, "src", "rules.def")

    try:
        rules = parse_rules_def(rules_def_path)
    except Exception as e:
        print(f"[ERROR] Failed parsing rules.def: {e}", file=sys.stderr)
        sys.exit(1)

    if args.check:
        errors = check_sync(rules, repo_root)
        if errors:
            print("[DRIFT DETECTED] The following rule synchronization issues were found:")
            for err in errors:
                print(f"  - {err}")
            sys.exit(1)
        else:
            print(f"[OK] rules.def ({len(rules)} rules) is in complete sync with models.py and fastpath.c")
            sys.exit(0)
    else:
        sync_models(rules, repo_root)
        errors = check_sync(rules, repo_root)
        if errors:
            print("[WARNING] Post-sync validation notes:")
            for err in errors:
                print(f"  - {err}")


if __name__ == "__main__":
    main()
