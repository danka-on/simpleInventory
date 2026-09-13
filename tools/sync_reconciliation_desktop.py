"""Synchronize reconciliation source with the Desktop monolithic checkout."""
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
desktop = Path('C:/Users/boxatron/Desktop/simpleInventory')
source = (root/'sweetshelves/listing_reconciliation.py').read_text(encoding='utf-8')
old = 'from . import database as ss_database, errors as ss_errors, listing_alerts as ss_listing_alerts'
existing = (desktop/'listing_reconciliation.py').read_text(encoding='utf-8')
adapter = existing[existing.index('from types import SimpleNamespace'):existing.index('# dismissed_alerts.alert_type')].rstrip()
assert source.count(old)==1
(desktop/'listing_reconciliation.py').write_text(source.replace(old,adapter),encoding='utf-8')
for name in ('reconciliation_ai.py','reconciliation_names.py','reconciliation_stock.py','test_reconciliation_ai.py',
             'templates/listing_reconciliation.html','test_listing_reconciliation.cjs'):
    shutil.copy2(root/name,desktop/name)
for name in ('reconciliation_vision.py','test_reconciliation_vision.py'):
    (desktop/name).unlink(missing_ok=True)
compile((desktop/'listing_reconciliation.py').read_text(encoding='utf-8'),'listing_reconciliation.py','exec')
print('Desktop reconciliation source synchronized and compiled.')
