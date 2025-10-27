"""
Test if DELETE FROM SEARCHRACK is actually working
"""
import sqlite3

search_conn = sqlite3.connect('searchRack.db')
search_cur = search_conn.cursor()

# Count before delete
search_cur.execute('SELECT COUNT(*) FROM SEARCHRACK')
before = search_cur.fetchone()[0]
print(f"Before DELETE: {before} rows")

# Try to delete
search_cur.execute('DELETE FROM SEARCHRACK')
print(f"DELETE executed")

# Count after delete (before commit)
search_cur.execute('SELECT COUNT(*) FROM SEARCHRACK')
after_delete = search_cur.fetchone()[0]
print(f"After DELETE (before commit): {after_delete} rows")

# Commit
search_conn.commit()

# Count after commit
search_cur.execute('SELECT COUNT(*) FROM SEARCHRACK')
after_commit = search_cur.fetchone()[0]
print(f"After COMMIT: {after_commit} rows")

search_conn.close()

print("\n✅ If this shows 0 rows after commit, the DELETE works fine.")
print("❌ If not, there might be a database lock or trigger preventing deletion.")
