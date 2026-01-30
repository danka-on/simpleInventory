"""
Fix sqlite3.connect() calls by adding try/finally wrappers.

Strategy A only (191 connections): Add finally clause to existing try/except blocks.
Strategy B (21 connections): Skip for now, handle separately.

Single-pass approach:
1. Pre-compute all changes as line annotations
2. Build output in one pass, never modifying indices
"""

import re
from collections import defaultdict

with open('app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

def get_indent(line):
    return len(line) - len(line.lstrip())

def find_enclosing_try(lines, connect_idx, connect_indent):
    i = connect_idx - 1
    while i >= max(connect_idx - 50, 0):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith('#'):
            i -= 1
            continue
        li = get_indent(lines[i])
        if stripped == 'try:' and li < connect_indent:
            return i, li
        if li < connect_indent and (
            stripped.startswith('if ') or stripped.startswith('elif ') or
            stripped.startswith('else:') or stripped.startswith('else ') or
            stripped.startswith('for ') or stripped.startswith('while ') or
            stripped.startswith('with ')
        ):
            i -= 1
            continue
        if li < connect_indent:
            break
        i -= 1
    return None, None

def find_close_range(lines, var_name, start_idx, base_indent):
    closes = []
    for i in range(start_idx + 1, min(start_idx + 500, len(lines))):
        stripped = lines[i].strip()
        if not stripped:
            continue
        line_indent = get_indent(lines[i])
        if line_indent < base_indent and (
            stripped.startswith('def ') or stripped.startswith('class ') or
            stripped.startswith('@app.') or
            (stripped.startswith('@') and 'route' in stripped)
        ):
            break
        if re.match(rf'^\s*{re.escape(var_name)}\s*=\s*sqlite3\.connect\(', lines[i]):
            break
        if stripped == f'{var_name}.close()':
            closes.append(i)
    if closes:
        return closes[-1], closes
    return None, []

def is_inside_try_with_finally_close(lines, connect_idx, var_name):
    _, try_indent = find_enclosing_try(lines, connect_idx, get_indent(lines[connect_idx]))
    if try_indent is None:
        return False
    for i in range(connect_idx + 1, min(connect_idx + 300, len(lines))):
        s = lines[i].strip()
        li = get_indent(lines[i])
        if s == 'finally:' and li == try_indent:
            for j in range(i + 1, min(i + 10, len(lines))):
                if f'{var_name}.close()' in lines[j].strip():
                    return True
                if lines[j].strip() and get_indent(lines[j]) <= try_indent:
                    break
            return False
        if s and li <= try_indent and not s.startswith('except') and s not in ('try:', 'finally:'):
            break
    return False

def find_except_clause(lines, try_idx, try_indent):
    for i in range(try_idx + 1, min(try_idx + 500, len(lines))):
        stripped = lines[i].strip()
        li = get_indent(lines[i])
        if li == try_indent and stripped.startswith('except'):
            return i
        if li == try_indent and stripped and not stripped.startswith('except') and stripped not in ('else:', 'finally:'):
            break
        if li < try_indent and stripped and (
            stripped.startswith('def ') or stripped.startswith('class ') or stripped.startswith('@')
        ):
            break
    return None

def find_try_block_end(lines, try_idx, try_indent):
    """Find the index of the first line AFTER the try/except/else/finally block."""
    i = try_idx + 1
    while i < len(lines):
        stripped = lines[i].strip()
        li = get_indent(lines[i])
        if stripped and li == try_indent:
            if stripped.startswith('except') or stripped == 'else:' or stripped == 'finally:':
                i += 1
                while i < len(lines):
                    s2 = lines[i].strip()
                    li2 = get_indent(lines[i])
                    if s2 and li2 <= try_indent:
                        break
                    i += 1
                continue
            else:
                return i
        elif stripped and li < try_indent:
            return i
        i += 1
    return len(lines)

# Collect entries
connect_entries = []
for i, line in enumerate(lines):
    if 'sqlite3.connect(' not in line or 'get_db_connection' in line:
        continue
    if i == 49:
        continue
    m = re.match(r'^(\s*)(\w+)\s*=\s*sqlite3\.connect\(', line)
    if not m:
        continue
    var_name = m.group(2)
    base_indent = len(m.group(1))

    if is_inside_try_with_finally_close(lines, i, var_name):
        continue

    last_close, all_closes = find_close_range(lines, var_name, i, base_indent)
    if last_close is None:
        continue

    try_idx, try_indent_val = find_enclosing_try(lines, i, base_indent)
    if try_idx is not None and find_except_clause(lines, try_idx, try_indent_val) is not None:
        strategy = 'A'
    else:
        strategy = 'B'

    connect_entries.append({
        'connect_idx': i,
        'var_name': var_name,
        'base_indent': base_indent,
        'closes': set(all_closes),
        'last_close': last_close,
        'strategy': strategy,
        'try_idx': try_idx,
        'try_indent': try_indent_val,
    })

a_entries = [e for e in connect_entries if e['strategy'] == 'A']
b_entries = [e for e in connect_entries if e['strategy'] == 'B']
print(f"Found {len(connect_entries)} connections: {len(a_entries)} Strategy A, {len(b_entries)} Strategy B")

# ---- Strategy A: Single-pass annotation approach ----
# For each try group, determine:
# 1. Which lines to DELETE (close lines)
# 2. Where to INSERT finally block (block_end)

# Group by try_idx
try_groups = defaultdict(list)
for entry in a_entries:
    try_groups[entry['try_idx']].append(entry)

# Build annotations: lines_to_delete, lines_to_insert_after
lines_to_delete = set()
# insert_after: {line_idx: [lines_to_insert]}
insert_after = defaultdict(list)

for try_idx, group in try_groups.items():
    try_indent_val = group[0]['try_indent']

    # Collect all close lines to remove
    all_closes = set()
    var_names = []
    for entry in group:
        all_closes.update(entry['closes'])
        if entry['var_name'] not in var_names:
            var_names.append(entry['var_name'])

    # Find block end (on original lines)
    block_end = find_try_block_end(lines, try_idx, try_indent_val)

    # Mark closes for deletion
    lines_to_delete.update(all_closes)

    # The block_end is where we insert finally
    # But block_end might shift due to deletions. Instead of tracking shifts,
    # we'll use a different approach: insert AFTER the last line of the block (block_end - 1)
    # We need to find the last line that's part of the try/except block
    last_block_line = block_end - 1

    # But last_block_line might be a blank line or a deleted close line
    # Go backwards to find the last real line of the block
    while last_block_line > try_idx and (
        lines[last_block_line].strip() == '' or
        last_block_line in lines_to_delete
    ):
        last_block_line -= 1

    # Build finally lines
    finally_indent = ' ' * try_indent_val
    finally_lines = [finally_indent + 'finally:\n']
    for var_name in var_names:
        finally_lines.append(finally_indent + f'    {var_name}.close()\n')

    insert_after[last_block_line].extend(finally_lines)

# ---- Strategy B: Also build as annotations ----
# For Strategy B, we need try/finally with re-indentation
# Handle each B entry as delete+insert
b_delete = set()
b_replace = {}  # {start_line: (end_line_exclusive, new_lines)}

for entry in b_entries:
    ci = entry['connect_idx']
    var_name = entry['var_name']
    base_indent = entry['base_indent']
    closes = entry['closes']
    last_close = entry['last_close']
    indent = ' ' * base_indent

    body_lines = []
    for i in range(ci + 1, last_close + 1):
        if i in closes:
            continue
        body_lines.append((i, lines[i]))

    while body_lines and body_lines[-1][1].strip() == '':
        body_lines.pop()

    has_code = any(l[1].strip() for l in body_lines)
    if not has_code:
        continue

    new_block = []
    new_block.append(lines[ci])
    new_block.append(indent + 'try:\n')

    # Track triple-string state
    in_triple = False
    tc = None
    for scan_i in range(ci + 1):
        scan_line = lines[scan_i]
        if in_triple:
            if tc in scan_line:
                in_triple = False
        else:
            for tq in ('"""', "'''"):
                if scan_line.count(tq) % 2 == 1:
                    in_triple = True
                    tc = tq
                    break

    for orig_idx, orig_line in body_lines:
        if orig_line.strip() == '':
            new_block.append(orig_line)
            continue
        if in_triple:
            new_block.append(orig_line)
            if tc in orig_line:
                in_triple = False
        else:
            curr_indent = get_indent(orig_line)
            if curr_indent >= base_indent:
                new_block.append('    ' + orig_line)
            else:
                new_block.append(orig_line)
            for tq in ('"""', "'''"):
                if orig_line.count(tq) % 2 == 1:
                    in_triple = True
                    tc = tq
                    break

    new_block.append(indent + 'finally:\n')
    new_block.append(indent + f'    {var_name}.close()\n')

    b_replace[ci] = (last_close + 1, new_block)

# ---- Build output in a single pass ----
output = []
i = 0
while i < len(lines):
    # Check if this line starts a Strategy B replacement range
    if i in b_replace:
        end, new_lines = b_replace[i]
        output.extend(new_lines)
        i = end
        continue

    # Check if this line should be deleted (Strategy A close)
    if i in lines_to_delete:
        i += 1
        continue

    # Output the line
    output.append(lines[i])

    # Check if we need to insert after this line
    if i in insert_after:
        output.extend(insert_after[i])

    i += 1

with open('app.py', 'w', encoding='utf-8') as f:
    f.writelines(output)

print(f"Applied Strategy A: {len(a_entries)}, Strategy B: {len(b_replace)}")

import py_compile
try:
    py_compile.compile('app.py', doraise=True)
    print("SYNTAX OK!")
except py_compile.PyCompileError as e:
    print(f"SYNTAX ERROR: {e}")
