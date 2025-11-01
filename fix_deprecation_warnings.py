"""
Fix all datetime.utcnow() deprecation warnings in app.py
Replace with datetime.now(datetime.UTC)
"""
import re

print('=== Fixing Deprecation Warnings ===\n')

# Read the file
with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Count occurrences
original_count = content.count('datetime.utcnow()')
_dt_count = content.count('_dt.datetime.utcnow()')

print(f'Found {original_count} occurrences of datetime.utcnow()')
print(f'Found {_dt_count} occurrences of _dt.datetime.utcnow()')
print(f'Total: {original_count + _dt_count}\n')

# Replace all occurrences
# Pattern 1: datetime.datetime.utcnow() -> datetime.datetime.now(datetime.UTC)
content = content.replace('datetime.datetime.utcnow()', 'datetime.datetime.now(datetime.UTC)')

# Pattern 2: _dt.datetime.utcnow() -> _dt.datetime.now(_dt.UTC)
content = content.replace('_dt.datetime.utcnow()', '_dt.datetime.now(_dt.UTC)')

# Write back
with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)

# Verify
with open('app.py', 'r', encoding='utf-8') as f:
    new_content = f.read()

remaining = new_content.count('utcnow()')
print(f'✅ Replacements complete!')
print(f'Remaining utcnow() calls: {remaining}')

if remaining == 0:
    print('\n🎉 All deprecation warnings fixed!')
else:
    print(f'\n⚠️ Warning: {remaining} utcnow() calls still remain (manual review needed)')
