# database.py
import sqlite3

class Database:
    def __init__(self):
        self.conn = sqlite3.connect('airdrop.db')
        self.cursor = self.conn.cursor()
        self.execute_query("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance REAL DEFAULT 0,
                referrals INTEGER DEFAULT 0,
                wallet TEXT
            )
        """)
        self.execute_query("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                status TEXT,
                wallet TEXT,
                tx_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.execute_query("""
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY
            )
        """)
        self.commit()

    def execute_query(self, query, params=()):
        self.cursor.execute(query, params)
        return self.cursor

    def commit(self):
        self.conn.commit()

    def __del__(self):
        self.conn.close()