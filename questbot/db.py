import aiosqlite, time

DB_PATH = "quests.db"

async def init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                discord_id TEXT PRIMARY KEY,
                user_token TEXT NOT NULL,
                added_at INTEGER NOT NULL,
                last_run INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1
            )
        """)
        await db.commit()

async def set_token(discord_id: str, token: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (discord_id, user_token, added_at)
            VALUES (?, ?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                user_token=excluded.user_token,
                added_at=excluded.added_at,
                enabled=1
        """, (discord_id, token, int(time.time())))
        await db.commit()

async def get_token(discord_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_token, enabled FROM users WHERE discord_id=?",
            (discord_id,)
        ) as cur:
            row = await cur.fetchone()
            return row if row else (None, 0)

async def remove_token(discord_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM users WHERE discord_id=?", (discord_id,))
        await db.commit()

async def all_enabled():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT discord_id, user_token FROM users WHERE enabled=1"
        ) as cur:
            return await cur.fetchall()

async def mark_run(discord_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET last_run=? WHERE discord_id=?",
                         (int(time.time()), discord_id))
        await db.commit()
