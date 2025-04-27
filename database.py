import sqlite3
import logging

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.conn = sqlite3.connect('airdrop.db')
        self.conn.row_factory = sqlite3.Row  # Enable row factory for dictionary-like access
        self.cursor = self.conn.cursor()

        # Create users table with referrer_id
        self.execute_query("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance REAL DEFAULT 0,
                referrals INTEGER DEFAULT 0,
                wallet TEXT,
                referrer_id INTEGER
            )
        """)

        # Create withdrawals table
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

        # Create banned_users table
        self.execute_query("""
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY
            )
        """)

        # Migrate existing users table to add referrer_id if it doesn't exist
        try:
            self.cursor.execute("SELECT referrer_id FROM users LIMIT 1")
        except sqlite3.OperationalError as e:
            if "no such column: referrer_id" in str(e):
                logger.info("Adding referrer_id column to users table")
                self.execute_query("ALTER TABLE users ADD COLUMN referrer_id INTEGER")
                logger.info("Successfully added referrer_id column")
            else:
                logger.error(f"Unexpected database error during migration: {e}")
                raise

        self.commit()

    def execute_query(self, query, params=()):
        self.cursor.execute(query, params)
        return self.cursor

    def commit(self):
        self.conn.commit()

    def __del__(self):
        self.conn.close()
