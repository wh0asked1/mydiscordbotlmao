import os, asyncio, logging, time, random
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
import db, quest_engine

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("questbot")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
active_tasks = {}

C_OK = discord.Color.green()
C_ERR = discord.Color.red()
C_INFO = discord.Color.blue()
C_WAIT = discord.Color.gold()

def E(title, desc="", color=C_INFO, footer=None):
    e = discord.Embed(title=title, description=desc, color=color, timestamp=discord.utils.utcnow())
    if footer: e.set_footer(text=footer)
    return e

@bot.event
async def on_ready():
    await db.init()
    await bot.tree.sync()
    log.info(f"logged in as {bot.user}")
    auto_loop.start()

async def _run_user(discord_id: str, token: str, inter=None):
    await db.set_status(discord_id, "running", int(time.time()))
    try:
        done, errors = await quest_engine.run_pass(token)
        if errors:
            await db.set_status(discord_id, "error", int(time.time()))
            if inter:
                desc = "\n".join(f"❌ {e}" for e in errors)
                if done: desc += "\n\n" + "\n".join(f"✅ {d}" for d in done)
                await inter.followup.send(embed=E("⚠️ Partial Pass", desc, C_ERR), ephemeral=True)
        else:
            await db.set_status(discord_id, "idle", int(time.time()))
            if inter:
                desc = "\n".join(f"✅ {d}" for d in done) if done else "Nothing new to claim."
                await inter.followup.send(embed=E("✅ Pass Complete", desc, C_OK), ephemeral=True)
    except asyncio.CancelledError:
        await db.set_status(discord_id, "idle")
        if inter:
            try: await inter.followup.send(embed=E("🛑 Stopped", "Pass cancelled by user.", C_WAIT), ephemeral=True)
            except Exception: pass
        raise
    except Exception as e:
        log.exception(e)
        await db.set_status(discord_id, "error", int(time.time()))
        if inter:
            try: await inter.followup.send(embed=E("❌ Crash", str(e), C_ERR), ephemeral=True)
            except Exception: pass
    finally:
        active_tasks.pop(discord_id, None)

@tasks.loop(minutes=30)
async def auto_loop():
    sem = asyncio.Semaphore(3)
    rows = await db.all_enabled()
    async def _one(discord_id, token):
        async with sem:
            if discord_id in active_tasks: return
            u = await db.get_user(discord_id)
            if u and u["status"] == "running": return
            await asyncio.sleep(random.uniform(1, 10))
            task = asyncio.create_task(_run_user(discord_id, token))
            active_tasks[discord_id] = task
    await asyncio.gather(*(_one(d, t) for d, t in rows), return_exceptions=True)

@auto_loop.before_loop
async def before():
    await bot.wait_until_ready()

# --- Commands ---

@bot.tree.command(name="ping", description="Check bot latency")
async def ping(inter: discord.Interaction):
    await inter.response.send_message(embed=E("🏓 Pong", f"Latency: **{round(bot.latency*1000)}ms**", C_INFO), ephemeral=True)

@bot.tree.command(name="settoken", description="Save your Discord user token")
async def settoken(inter: discord.Interaction, token: str):
    await inter.response.defer(ephemeral=True)
    me = await quest_engine.check_token(token)
    if not me:
        return await inter.followup.send(embed=E("❌ Invalid Token", "Check the token and try again.", C_ERR), ephemeral=True)
    await db.set_token(str(inter.user.id), token)
    e = E("✅ Token Saved", f"Bound to **{me.get('username')}**", C_OK, "Delete your command message if it was public.")
    await inter.followup.send(embed=e, ephemeral=True)

@bot.tree.command(name="token", description="Check your saved token status")
async def token_cmd(inter: discord.Interaction):
    u = await db.get_user(str(inter.user.id))
    if not u:
        return await inter.response.send_message(embed=E("❌ No Token", "Run `/settoken` first.", C_ERR), ephemeral=True)
    me = await quest_engine.check_token(u["user_token"])
    icon = "🟢" if u["enabled"] else "🔴"
    e = E("🔑 Token Status", color=C_INFO)
    e.add_field(name="Account", value=f"`{me.get('username') if me else 'Dead'}`", inline=False)
    e.add_field(name="Auto-Farm", value=f"{icon} {'Enabled' if u['enabled'] else 'Disabled'}", inline=True)
    e.add_field(name="State", value=f"`{u['status'].upper()}`", inline=True)
    await inter.response.send_message(embed=e, ephemeral=True)

@bot.tree.command(name="removetoken", description="Remove your saved token")
async def removetoken(inter: discord.Interaction):
    await db.remove_token(str(inter.user.id))
    await inter.response.send_message(embed=E("🗑️ Token Removed", "All traces gone.", C_OK), ephemeral=True)

@bot.tree.command(name="start", description="Run a quest pass right now")
async def start(inter: discord.Interaction):
    await inter.response.defer(ephemeral=True)
    u = await db.get_user(str(inter.user.id))
    if not u or not u["user_token"]:
        return await inter.followup.send(embed=E("❌ No Token", "Run `/settoken` first.", C_ERR), ephemeral=True)
    if str(inter.user.id) in active_tasks:
        return await inter.followup.send(embed=E("⏳ Already Running", "Wait for the current pass.", C_WAIT), ephemeral=True)
    await inter.followup.send(embed=E("🚀 Starting Pass", "Harvesting quests...", C_WAIT), ephemeral=True)
    task = asyncio.create_task(_run_user(str(inter.user.id), u["user_token"], inter))
    active_tasks[str(inter.user.id)] = task

@bot.tree.command(name="stop", description="Stop current quest pass")
async def stop(inter: discord.Interaction):
    task = active_tasks.get(str(inter.user.id))
    if not task:
        return await inter.response.send_message(embed=E("⚠️ Not Running", "No active pass.", C_WAIT), ephemeral=True)
    task.cancel()
    await inter.response.send_message(embed=E("🛑 Stop Signal Sent", "Task will halt momentarily.", C_OK), ephemeral=True)

@bot.tree.command(name="status", description="Check quest bot status")
async def status(inter: discord.Interaction):
    u = await db.get_user(str(inter.user.id))
    if not u:
        return await inter.response.send_message(embed=E("❌ No Token", "Run `/settoken` first.", C_ERR), ephemeral=True)
    state = u["status"] or "idle"
    color = C_OK if state == "idle" else C_WAIT if state == "running" else C_ERR
    e = E("📊 Status", color=color)
    e.add_field(name="State", value=f"**{state.upper()}**", inline=True)
    e.add_field(name="Last Run", value=f"<t:{u['last_run']}:R>" if u['last_run'] else "Never", inline=True)
    await inter.response.send_message(embed=e, ephemeral=True)

if __name__ == "__main__":
    try:
        bot.run(BOT_TOKEN)
    finally:
        asyncio.run(quest_engine.close())
        asyncio.run(db.close())
