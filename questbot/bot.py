import os, asyncio, logging
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import db, quest_engine

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("questbot")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await db.init()
    await bot.tree.sync()
    log.info(f"logged in as {bot.user}")
    auto_loop.start()

@tasks.loop(minutes=30)
async def auto_loop():
    rows = await db.all_enabled()
    log.info(f"[auto] {len(rows)} user(s)")
    for discord_id, token in rows:
        try:
            await quest_engine.run_pass(token, log=lambda m: log.info(f"[{discord_id}] {m}"))
            await db.mark_run(discord_id)
        except Exception as e:
            log.exception(f"[{discord_id}] {e}")
        await asyncio.sleep(5)

@auto_loop.before_loop
async def before():
    await bot.wait_until_ready()

# --- commands ---

@bot.tree.command(name="settoken", description="Save your Discord user token for quest farming")
async def settoken(inter: discord.Interaction, token: str):
    await inter.response.defer(ephemeral=True)
    me = await quest_engine.check_token(token)
    if not me:
        return await inter.followup.send("❌ token invalid or dead.", ephemeral=True)
    await db.set_token(str(inter.user.id), token)
    # scrub the token from channel
    try:
        await inter.channel.delete_messages([inter.message]) if inter.message else None
    except Exception:
        pass
    await inter.followup.send(
        f"✅ saved for **{me.get('username')}**. "
        f"⚠️ delete your `!settoken` message — the token is in your chat history.",
        ephemeral=True,
    )

@bot.tree.command(name="runtoken", description="Run a quest pass right now")
async def runtoken(inter: discord.Interaction):
    await inter.response.defer(ephemeral=True)
    token, enabled = await db.get_token(str(inter.user.id))
    if not token or not enabled:
        return await inter.followup.send("❌ no token saved. use `/settoken`.", ephemeral=True)
    await inter.followup.send("⏳ pass started…", ephemeral=True)
    done = await quest_engine.run_pass(token, log=lambda m: log.info(f"[{inter.user.id}] {m}"))
    if not done:
        await inter.followup.send("✅ pass complete — nothing new to claim.", ephemeral=True)
    else:
        await inter.followup.send(
            "✅ claimed:\n" + "\n".join(f"• {n}" for n in done),
            ephemeral=True,
        )

@bot.tree.command(name="mytoken", description="Check status")
async def mytoken(inter: discord.Interaction):
    token, enabled = await db.get_token(str(inter.user.id))
    if not token:
        return await inter.response.send_message("❌ none saved.", ephemeral=True)
    me = await quest_engine.check_token(token)
    if not me:
        return await inter.response.send_message("❌ saved token is dead.", ephemeral=True)
    await inter.response.send_message(
        f"✅ active as **{me.get('username')}** (enabled={bool(enabled)})",
        ephemeral=True,
    )

@bot.tree.command(name="removetoken", description="Delete your saved token")
async def removetoken(inter: discord.Interaction):
    await db.remove_token(str(inter.user.id))
    await inter.response.send_message("🗑️ removed.", ephemeral=True)

@bot.tree.command(name="toggle", description="Enable/disable your auto farm")
async def toggle(inter: discord.Interaction):
    token, enabled = await db.get_token(str(inter.user.id))
    if not token:
        return await inter.response.send_message("❌ no token saved.", ephemeral=True)
    import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as d:
        await d.execute("UPDATE users SET enabled=? WHERE discord_id=?",
                        (0 if enabled else 1, str(inter.user.id)))
        await d.commit()
    await inter.response.send_message(
        f"🔁 auto farm {'disabled' if enabled else 'enabled'}.",
        ephemeral=True,
    )

@bot.tree.command(name="faq", description="How this bot works")
async def faq(inter: discord.Interaction):
    await inter.response.send_message(
        "**quest bot** — farms Discord quests for you.\n"
        "`/settoken <token>` — save your user token (DM yourself, delete after)\n"
        "`/runtoken` — run one pass now\n"
        "`/mytoken` — check status\n"
        "`/toggle` — enable/disable auto\n"
        "`/removetoken` — delete it\n\n"
        "Auto loop every 30 min. Only supports video/play/activity quests. "
        "This is a **selfbot on your token** — Discord ToS violation, "
        "burner account recommended. You've been told.",
        ephemeral=True,
    )

bot.run(BOT_TOKEN)
