"""Script to update src/X.py references to src/mdrap/X.py across docs."""

import os
import re
import glob

def run():
    mdrap_dir = os.path.join("src", "mdrap")
    mdrap_modules = set(
        os.path.splitext(f)[0]
        for f in os.listdir(mdrap_dir)
        if f.endswith(".py")
    )

    pattern = re.compile(r'src/([a-zA-Z0-9_]+)\.py')

    total_files = 0
    total_replacements = 0

    for path in glob.glob("docs/**/*.md", recursive=True):
        with open(path, "r", encoding="utf-8", errors="ignore") as fp:
            content = fp.read()

        def repl(match):
            nonlocal total_replacements
            mod = match.group(1)
            # Only replace if module actually exists in src/mdrap and is not cli
            if mod in mdrap_modules and mod != "cli":
                total_replacements += 1
                return f"src/mdrap/{mod}.py"
            return match.group(0)

        new_content = pattern.sub(repl, content)
        if new_content != content:
            with open(path, "w", encoding="utf-8") as fp:
                fp.write(new_content)
            total_files += 1
            print(f"Updated {path}")

    print(f"Completed: updated {total_replacements} references across {total_files} files.")

if __name__ == "__main__":
    run()
