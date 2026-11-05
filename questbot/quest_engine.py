import asyncio, json, random, re, base64, time, aiohttp
from datetime import datetime, timezone
from typing import Optional

API = "https://discord.com/api/v9"
HEARTBEAT_INTERVAL = 20
SUPPORTED_TASKS = ["WATCH_VIDEO", "PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP", "PLAY_ACTIVITY", "WATCH_VIDEO_ON_MOBILE"]

# ── Build number fetcher ───────────────────────────────────────────────────────
async def fetch_latest_build_number(session: aiohttp.ClientSession) -> int:
    FALLBACK = 504649
    try:
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        async with session.get("https://discord.com/app", headers={"User-Agent": ua}, timeout=15) as r:
            if r.status != 200:
                return FALLBACK
            html = await r.text()
            
        scripts = re.findall(r'/assets/([a-f0-9]+)\.js', html)
        if not scripts:
            scripts_alt = re.findall(r'src="(/assets/[^"]+\.js)"', html)
            scripts = [s.split('/')[-1].replace('.js', '') for s in scripts_alt]

        if not scripts:
            return FALLBACK

        for asset_hash in scripts[-5:]:
            try:
                async with session.get(f"https://discord.com/assets/{asset_hash}.js", headers={"User-Agent": ua}, timeout=15) as ar:
                    if ar.status == 200:
                        text = await ar.text()
                        m = re.search(r'buildNumber["\s:]+["\s]*(\d{5,7})', text)
                        if m:
                            return int(m.group(1))
            except Exception:
                continue
        return FALLBACK
    except Exception:
        return FALLBACK

def make_super_properties(build_number: int) -> str:
    obj = {
        "os": "Windows", "browser": "Discord Client", "release_channel": "stable",
        "client_version": "1.0.9175", "os_version": "10.0.26100",
        "os_arch": "x64", "app_arch": "x64", "system_locale": "en-US",
        "browser_user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9175 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36",
        "browser_version": "32.2.7", "client_build_number": build_number,
        "native_build_number": 59498, "client_event_source": None,
    }
    return base64.b64encode(json.dumps(obj).encode()).decode()

# ── Helpers ────────────────────────────────────────────────────────────────────
def _get(d: Optional[dict], *keys):
    if d is None: return None
    for k in keys:
        if k in d: return d[k]
    return None

def get_task_config(quest: dict) -> Optional[dict]:
    return _get(quest.get("config", {}), "taskConfig", "task_config", "taskConfigV2", "task_config_v2")

def get_quest_name(quest: dict) -> str:
    cfg = quest.get("config", {})
    msgs = cfg.get("messages", {})
    name = _get(msgs, "questName", "quest_name") or _get(msgs, "gameTitle", "game_title")
    return (name or cfg.get("application", {}).get("name") or f"Quest#{quest.get('id', '?')}").strip()

def get_task_type(quest: dict) -> Optional[str]:
    tc = get_task_config(quest)
    if not tc or "tasks" not in tc: return None
    for t in SUPPORTED_TASKS:
        if tc["tasks"].get(t) is not None: return t
    return None

def get_seconds_needed(quest: dict) -> int:
    tc, t = get_task_config(quest), get_task_type(quest)
    return tc["tasks"][t].get("target", 0) if tc and t else 0

def get_seconds_done(quest: dict) -> float:
    t = get_task_type(quest)
    if not t: return 0
    return (quest.get("userStatus") or {}).get("progress", {}).get(t, {}).get("value", 0)

def is_completable(quest: dict) -> bool:
    exp = _get(quest.get("config", {}), "expiresAt", "expires_at")
    if exp:
        try:
            if datetime.fromisoformat(exp.replace("Z", "+00:00")) <= datetime.now(timezone.utc): return False
        except: pass
    return get_task_type(quest) is not None

# ── Engine ─────────────────────────────────────────────────────────────────────
_session = None
_session_lock = asyncio.Lock()

async def get_session():
    global _session
    if _session is None or _session.closed:
        async with _session_lock:
            if _session is None or _session.closed:
                _session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=20, ttl_dns_cache=300), timeout=aiohttp.ClientTimeout(total=15))
    return _session

async def _api(session, hdrs, method, path, payload=None):
    backoff = 1.0
    for attempt in range(3):
        try:
            kw = {"json": payload} if payload else {}
            async with session.request(method, f"{API}{path}", headers=hdrs, **kw) as r:
                if r.status == 429:
                    await asyncio.sleep(float((await r.json()).get("retry_after", 5)) + 1)
                    continue
                if r.status >= 500 and attempt < 2:
                    await asyncio.sleep(backoff); backoff *= 2; continue
                txt = await r.text()
                if r.status >= 400: return {"error": r.status, "text": txt}
                return json.loads(txt) if txt else {}
        except Exception as e:
            if attempt == 2: return {"error": str(e)}
            await asyncio.sleep(backoff); backoff *= 2
    return {"error": "max_retries"}

async def check_token(token: str):
    s = await get_session()
    try:
        async with s.get(f"{API}/users/@me", headers={"Authorization": token}, timeout=10) as r:
            if r.status == 200: return await r.json()
    except: pass
    return None

async def run_pass(token: str, log=print):
    s = await get_session()
    done, errors = [], []
    
    log("Fetching build number...")
    build_num = await fetch_latest_build_number(s)
    log(f"Using build number: {build_num}")

    hdrs = {
        "Authorization": token, "Content-Type": "application/json", "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9175 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36",
        "X-Super-Properties": make_super_properties(build_num),
        "X-Discord-Locale": "en-US",
        "X-Discord-Timezone": "Asia/Ho_Chi_Minh",
        "Origin": "https://discord.com",
        "Referer": "https://discord.com/channels/@me",
    }

    me = await _api(s, hdrs, "GET", "/users/@me")
    if "error" in me: return done, ["Token invalid"]

    log(f"Logged in: {me.get('username')}")
    res = await _api(s, hdrs, "GET", "/quests/@me")
    
    if isinstance(res, dict):
        quests = res.get("quests", [])
        if res.get("quest_enrollment_blocked_until"):
            log(f"Enrollment blocked until: {res['quest_enrollment_blocked_until']}")
    elif isinstance(res, list):
        quests = res
    else:
        return done, ["Bad quest response"]

    for q in quests:
        if not is_completable(q): continue
        us = q.get("userStatus") or {}
        if us.get("completedAt"): continue
        
        qname = get_quest_name(q)
        qid = q.get("id")
        ttype = get_task_type(q)
        seconds_needed = get_seconds_needed(q)
        seconds_done = get_seconds_done(q)

        if not us.get("enrolledAt"):
            log(f"Enrolling: {qname}")
            r = await _api(s, hdrs, "POST", f"/quests/{qid}/enroll", payload={
                "location": 11, "is_targeted": False, "metadata_raw": None, "metadata_sealed": None,
                "traffic_metadata_raw": q.get("traffic_metadata_raw"), "traffic_metadata_sealed": q.get("traffic_metadata_sealed"),
            })
            if "error" in r:
                log(f"Enroll failed: {r.get('text')}")
                continue
            await asyncio.sleep(3)
            seconds_done = 0

        if ttype == "WATCH_VIDEO" or ttype == "WATCH_VIDEO_ON_MOBILE":
            log(f"Video: {qname} ({seconds_done:.0f}/{seconds_needed}s)")
            enrolled_ts = datetime.fromisoformat(us["enrolledAt"].replace("Z", "+00:00")).timestamp() if us.get("enrolledAt") else time.time()
            speed = 7
            while seconds_done < seconds_needed:
                max_allowed = (time.time() - enrolled_ts) + 10
                diff = max_allowed - seconds_done
                timestamp = seconds_done + speed
                if diff >= speed:
                    r = await _api(s, hdrs, "POST", f"/quests/{qid}/video-progress", payload={"timestamp": min(seconds_needed, timestamp + random.random())})
                    if "error" not in r:
                        if r.get("completed_at"): break
                        seconds_done = min(seconds_needed, timestamp)
                        log(f"  [{qname}] {seconds_done:.0f}/{seconds_needed}s")
                    elif r.get("error") == 429:
                        await asyncio.sleep(float(r.get("text", 5)) + 1) # rough
                if timestamp >= seconds_needed: break
                await asyncio.sleep(1)
            await _api(s, hdrs, "POST", f"/quests/{qid}/video-progress", payload={"timestamp": seconds_needed})

        elif ttype == "PLAY_ACTIVITY":
            log(f"Activity: {qname} ({seconds_done:.0f}/{seconds_needed}s)")
            stream_key = "call:0:1"
            while seconds_done < seconds_needed:
                r = await _api(s, hdrs, "POST", f"/quests/{qid}/heartbeat", payload={"stream_key": stream_key, "terminal": False})
                if "error" not in r:
                    seconds_done = r.get("progress", {}).get("PLAY_ACTIVITY", {}).get("value", seconds_done)
                    log(f"  [{qname}] {seconds_done:.0f}/{seconds_needed}s")
                    if r.get("completed_at") or seconds_done >= seconds_needed: break
                await asyncio.sleep(HEARTBEAT_INTERVAL)
            await _api(s, hdrs, "POST", f"/quests/{qid}/heartbeat", payload={"stream_key": stream_key, "terminal": True})

        elif ttype in ("PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP"):
            log(f"Desktop: {qname} ({seconds_done:.0f}/{seconds_needed}s)")
            pid = random.randint(1000, 30000)
            while seconds_done < seconds_needed:
                r = await _api(s, hdrs, "POST", f"/quests/{qid}/heartbeat", payload={"stream_key": f"call:0:{pid}", "terminal": False})
                if "error" not in r:
                    pd = r.get("progress", {})
                    if ttype in pd: seconds_done = pd[ttype].get("value", seconds_done)
                    log(f"  [{qname}] {seconds_done:.0f}/{seconds_needed}s")
                    if r.get("completed_at") or seconds_done >= seconds_needed: break
                await asyncio.sleep(HEARTBEAT_INTERVAL)
            await _api(s, hdrs, "POST", f"/quests/{qid}/heartbeat", payload={"stream_key": f"call:0:{pid}", "terminal": True})

        done.append(qname)
        log(f"Completed: {qname}")
        await asyncio.sleep(2)

    return done, errors
