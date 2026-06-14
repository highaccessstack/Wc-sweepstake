#!/usr/bin/env python3
"""Validate JS syntax in WorldCupDashboard.html by extracting <script> blocks and checking with Node."""
import re
import subprocess
import sys
import tempfile
import os

HTML_FILE = os.path.join(os.path.dirname(__file__), '..', 'WorldCupDashboard.html')


def check_string_apostrophes(html):
    """Catch bare ASCII apostrophes inside single-quoted JS string keys/values."""
    issues = []
    # Match single-quoted JS strings and check for unescaped ' inside
    for m in re.finditer(r"'([^'\\\n]*(?:\\.[^'\\\n]*)*)'", html):
        val = m.group(1)
        if "'" in val:  # should never happen — means the regex matched wrong
            line = html[:m.start()].count('\n') + 1
            issues.append(f'L{line}: possible apostrophe-in-single-quote: {m.group()[:60]}')
    # Simpler heuristic: find patterns like 'word'word' on a single line
    for i, line in enumerate(html.splitlines(), 1):
        if re.search(r"'[^']*[a-zA-Z]'[a-zA-Z]", line):
            issues.append(f'L{i}: possible mid-string apostrophe: {line.strip()[:80]}')
    return issues


def main():
    with open(HTML_FILE, encoding='utf-8') as f:
        html = f.read()

    # Pre-check: apostrophes inside single-quoted strings
    apos_issues = check_string_apostrophes(html)
    if apos_issues:
        print('WARNING: possible apostrophe-in-string issues:')
        for issue in apos_issues[:10]:
            print(' ', issue)

    blocks = re.findall(r'<script(?:\s[^>]*)?>(.*?)</script>', html, re.DOTALL)
    if not blocks:
        print('ERROR: no <script> blocks found')
        sys.exit(1)

    combined = '\n'.join(blocks)

    with tempfile.NamedTemporaryFile(suffix='.js', mode='w', encoding='utf-8', delete=False) as tmp:
        tmp.write(combined)
        tmp_path = tmp.name

    try:
        result = subprocess.run(['node', '--check', tmp_path], capture_output=True, text=True)
    except FileNotFoundError:
        os.unlink(tmp_path)
        print('SKIP: node not found — run in CI or install Node locally to validate')
        sys.exit(0)

    try:
        if result.returncode != 0:
            # Remap temp file line numbers back to HTML line numbers
            offset = 0
            for i, block in enumerate(blocks):
                lines_before = html[:html.find(block)].count('\n') + 1
                print(f'Script block {i+1} starts at HTML line ~{lines_before}')
                offset += block.count('\n') + 1
            print('\nNode syntax error:')
            print(result.stderr)
            sys.exit(1)
        print('OK: JS syntax valid')
    finally:
        os.unlink(tmp_path)


if __name__ == '__main__':
    main()
