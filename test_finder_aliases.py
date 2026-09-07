import sqlite3
import unittest

from finder_aliases import update_alias, load_aliases, forget_alias, barcode_key


class AliasTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.addCleanup(self.conn.close)

    def rows(self):
        return self.conn.execute('SELECT source_key, barcode_key, title FROM finder_aliases ORDER BY source_key').fetchall()

    def test_confirm_rematch_and_undo_restore_previous(self):
        first = update_alias(self.conn, '1', '001234567890-2', 'Gallery frame')
        second = update_alias(self.conn, '1', '999999999999', 'Gallery frame')
        update_alias(self.conn, '1', undo_token=second)
        self.assertEqual(self.rows(), [('1', '1234567890-2', 'Gallery frame')])
        # Consumed and obsolete tokens cannot remove restored evidence.
        update_alias(self.conn, '1', undo_token=second)
        update_alias(self.conn, '1', undo_token=first)
        self.assertEqual(len(self.rows()), 1)

    def test_undo_keeps_other_confirmations(self):
        update_alias(self.conn, '1', '123456789012', 'Gallery frame')
        token = update_alias(self.conn, '2', '123456789012', 'Gallery frame')
        update_alias(self.conn, '2', undo_token=token)
        self.assertEqual(len(self.rows()), 1)

    def test_forget_all_duplicate_evidence_and_no_undo_resurrection(self):
        update_alias(self.conn, '1', '123456789012', 'Gallery frame')
        update_alias(self.conn, '2', '123456789012', 'Gallery frame')
        token = update_alias(self.conn, '2', '999999999999', 'Different frame')
        alias_id = self.conn.execute("SELECT id FROM finder_aliases WHERE source_key='1'").fetchone()[0]
        forget_alias(self.conn, alias_id)
        update_alias(self.conn, '2', undo_token=token)
        self.assertEqual(self.rows(), [])

    def test_forget_new_alias_then_undo_does_not_restore_old_alias(self):
        update_alias(self.conn, '1', '123456789012', 'First')
        token = update_alias(self.conn, '1', '999999999999', 'Second')
        alias_id = self.conn.execute('SELECT id FROM finder_aliases').fetchone()[0]
        forget_alias(self.conn, alias_id)
        update_alias(self.conn, '1', undo_token=token)
        self.assertEqual(self.rows(), [])

    def test_rollback_and_suffix_isolation(self):
        update_alias(self.conn, '1', '001234567890-2', 'Original')
        self.conn.commit()
        try:
            with self.conn:
                update_alias(self.conn, '1', '999999999999', 'Failed match')
                raise RuntimeError('match failed')
        except RuntimeError:
            pass
        self.assertEqual(self.rows()[0][2], 'Original')
        self.assertEqual(barcode_key('001234567890-2'), barcode_key('1234567890-2'))
        self.assertNotEqual(barcode_key('001234567890-2'), barcode_key('1234567890-3'))

    def test_source_scoped_undo(self):
        token = update_alias(self.conn, '1', '123456789012', 'Gallery frame')
        update_alias(self.conn, '2', undo_token=token)
        self.assertEqual(len(self.rows()), 1)


if __name__ == '__main__':
    unittest.main()
