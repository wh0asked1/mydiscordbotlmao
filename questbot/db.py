import aiosqlite, time, asyncio

DB_PATH = "quests.db"
_pool = None
_lock = asyncio.Lock()

async def init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                discord_id TEXT PRIMARY KEY,
                user_token TEXT NOT NULL,
                added_at INTEGER NOT NULL,
                last_run INTEGER DEFAULT 0,
                status TEXT DEFAULT 'idle',
                enabled INTEGER DEFAULT 1
            )
        """)
        await db.commit()

async def _conn():
    global _pool
    if _pool is None:
        async with _lock:
            if _pool is None:
                _pool = await aiosqlite.connect(DB_PATH)
                await _pool.execute("PRAGMA journal_mode=WAL")
                await _pool.execute("PRAGMA synchronous=NORMAL")
                _pool.row_factory = aiosqlite.Row
    return _pool

async def set_token(discord_id: str, token: str):
    db = await _conn()
    await db.execute("""
        INSERT INTO users (discord_id, user_token, added_at, status)
        VALUES (?, ?, ?, 'idle')
        ON CONFLICT(discord_id) DO UPDATE SET
            user_token=excluded.user_token,
            added_at=excluded.added_at,
            status='idle', enabled=1
    """, (discord_id, token, int(time.time())))
    await db.commit()

async def get_user(discord_id: str):
    db = await _conn()
    async with db.execute(
        "SELECT user_token, enabled, status, last_run FROM users WHERE discord_id=?",
        (discord_id,)
    ) as cur:
        return await cur.fetchone()

async def set_status(discord_id: str, status: str, last_run: int = 0):
    db = await _conn()
    await db.execute("UPDATE users SET status=?, last_run=? WHERE discord_id=?",
                     (status, last_run, discord_id))
    await db.commit()

async def remove_token(discord_id: str):
    db = await _conn()
    await db.execute("DELETE FROM users WHERE discord_id=?", (discord_id,))
    await db.commit()

async def all_enabled():
    db = await _conn()
    async with db.execute("SELECT discord_id, user_token FROM users WHERE enabled=1") as cur:
        return await cur.fetchall()
