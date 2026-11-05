import asyncio, json, random, re, base64, time, aiohttp
from datetime import datetime, timezone
from typing import Optional

API = "https://discord.com/api/v9"
HEARTBEAT_INTERVAL = 20
SUPPORTED_TASKS = ["WATCH_VIDEO", "PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP", "PLAY_ACTIVITY", "WATCH_VIDEO_ON_MOBILE"]

_session = None
_session_lock = asyncio.Lock()

async def get_session():
    global _session
    if _session is None or _session.closed:
        async with _session_lock:
            if _session is None or _session.closed:
                _session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=20, ttl_dns_cache=300), timeout=aiohttp.ClientTimeout(total=15))
    return _session

async def fetch_latest_build_number(session: aiohttp.ClientSession) -> int:
    FALLBACK = 504649
    try:
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        async with session.get("https://discord.com/app", headers={"User-Agent": ua}, timeout=15) as r:
            if r.status != 200: return FALLBACK
            html = await r.text()
            
        scripts = re.findall(r'/assets/([a-f0-9]+)\.js', html)
        if not scripts:
            scripts_alt = re.findall(r'src="(/assets/[^"]+\.js)"', html)
            scripts = [s.split('/')[-1].replace('.js', '') for s in scripts_alt]
        if not scripts: return FALLBACK

        for asset_hash in scripts[-5:]:
            try:
                async with session.get(f"https://discord.com/assets/{asset_hash}.js", headers={"User-Agent": ua}, timeout=15) as ar:
                    if ar.status == 200:
                        text = await ar.text()
                        m = re.search(r'buildNumber["\s:]+["\s]*(\d{5,7})', text)
                        if m: return int(m.group(1))
            except Exception: continue
        return FALLBACK
    except Exception: return FALLBACK

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

def get_expires_at(quest: dict) -> Optional[str]:
    return _get(quest.get("config", {}), "expiresAt", "expires_at")

def get_user_status(quest: dict) -> dict:
    us = _get(quest, "userStatus", "user_status")
    return us if isinstance(us, dict) else {}

def is_completable(quest: dict) -> bool:
    expires = get_expires_at(quest)
    if expires:
        try:
            if datetime.fromisoformat(expires.replace("Z", "+00:00")) <= datetime.now(timezone.utc): return False
        except: pass
    tc = get_task_config(quest)
    if not tc or "tasks" not in tc: return False
    return any(tc["tasks"].get(t) is not None for t in SUPPORTED_TASKS)

def is_enrolled(quest: dict) -> bool:
    return bool(_get(get_user_status(quest), "enrolledAt", "enrolled_at"))

def is_completed(quest: dict) -> bool:
    return bool(_get(get_user_status(quest), "completedAt", "completed_at"))

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
    return (get_user_status(quest).get("progress", {})).get(t, {}).get("value", 0)

def get_enrolled_at(quest: dict) -> Optional[str]:
    return _get(get_user_status(quest), "enrolledAt", "enrolled_at")

async def _api(session, hdrs, method, path, payload=None):
    backoff = 1.0
    for attempt in range(3):
        try:
            kw = {"json": payload} if payload else {}
            async with session.request(method, f"{API}{path}", headers=hdrs, **kw) as r:
                txt = await r.text()
                if r.status == 429:
                    retry = float(json.loads(txt).get("retry_after", 5)) if txt else 5
                    await asyncio.sleep(retry + 1)
                    continue
                if r.status >= 500 and attempt < 2:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                if r.status >= 400:
                    return {"error": r.status, "text": txt}
                return json.loads(txt) if txt else {}
        except Exception as e:
            if attempt == 2: return {"error": str(e)}
            await asyncio.sleep(backoff)
            backoff *= 2
    return {"error": "max_retries"}

async def check_token(token: str):
    s = await get_session()
    try:
        async with s.get(f"{API}/users/@me", headers={"Authorization": token}, timeout=10) as r:
            if r.status == 200: return await r.json()
    except: pass
    return None

async def run_pass(token: str, update_cb=None):
    async def log(msg, level="info"):
        if update_cb:
            await update_cb(msg, level)
        else:
            print(f"[{level.upper()}] {msg}")

    s = await get_session()
    done, errors = [], []
    
    await log("Fetching build number...", "info")
    build_num = await fetch_latest_build_number(s)
    
    hdrs = {
        "Authorization": token, "Content-Type": "application/json", "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9175 Chrome/128.0.6613.186 Electron/32.2.7 Safari/537.36",
        "X-Super-Properties": make_super_properties(build_num),
        "X-Discord-Locale": "en-US", "X-Discord-Timezone": "Asia/Ho_Chi_Minh",
        "Origin": "https://discord.com", "Referer": "https://discord.com/channels/@me",
    }

    me = await _api(s, hdrs, "GET", "/users/@me")
    if "error" in me:
        await log(f"Token invalid: {me.get('text')}", "error")
        return done, ["Token invalid"]

    res = await _api(s, hdrs, "GET", "/quests/@me")
    
    if isinstance(res, dict) and "error" in res:
        err_text = res.get("text", "Unknown error")
        await log(f"Quest fetch error ({res.get('error')}): {err_text[:100]}", "error")
        return done, [f"API Error {res.get('error')}: {err_text[:100]}"]
    
    if isinstance(res, dict) and "quests" in res:
        quests = res["quests"]
    elif isinstance(res, list):
        quests = res
    else:
        return done, ["Bad quest response format"]

    await log(f"Found {len(quests)} quests. Processing...", "info")
    
    unaccepted = [q for q in quests if not is_enrolled(q) and not is_completed(q) and is_completable(q)]
    if unaccepted:
        await log(f"Auto-accepting {len(unaccepted)} quests...", "info")
        for q in unaccepted:
            r = await _api(s, hdrs, "POST", f"/quests/{q['id']}/enroll", payload={
                "location": 11, "is_targeted": False, "metadata_raw": None, "metadata_sealed": None,
                "traffic_metadata_raw": q.get("traffic_metadata_raw"), "traffic_metadata_sealed": q.get("traffic_metadata_sealed"),
            })
            if "error" not in r:
                await log(f"Accepted: {get_quest_name(q)}", "ok")
            await asyncio.sleep(3)
        await asyncio.sleep(2)
        res = await _api(s, hdrs, "GET", "/quests/@me")
        quests = res.get("quests", []) if isinstance(res, dict) else res

    for q in quests:
        if is_completed(q): continue
        if not is_enrolled(q): continue
        task_type = get_task_type(q)
        if not task_type: continue

        name = get_quest_name(q)
        qid = q["id"]
        seconds_needed = get_seconds_needed(q)
        seconds_done = get_seconds_done(q)

        if task_type in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
            await log(f"🎬 Video: {name} ({seconds_done:.0f}/{seconds_needed}s)", "info")
            enrolled_ts = datetime.fromisoformat(get_enrolled_at(q).replace("Z", "+00:00")).timestamp() if get_enrolled_at(q) else time.time()
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
                        await log(f"  [{name}] {seconds_done:.0f}/{seconds_needed}s", "progress")
                    elif r.get("error") == 429:
                        await asyncio.sleep(float(r.get("text", 5)) + 1)
                        continue
                if timestamp >= seconds_needed: break
                await asyncio.sleep(1)
            await _api(s, hdrs, "POST", f"/quests/{qid}/video-progress", payload={"timestamp": seconds_needed})
            done.append(name)
            await log(f"✅ Completed: {name}", "ok")

        elif task_type in ("PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP"):
            await log(f"🎮 {task_type}: {name} ({seconds_done:.0f}/{seconds_needed}s)", "info")
            pid = random.randint(1000, 30000)
            while seconds_done < seconds_needed:
                r = await _api(s, hdrs, "POST", f"/quests/{qid}/heartbeat", payload={"stream_key": f"call:0:{pid}", "terminal": False})
                if "error" not in r:
                    pd = r.get("progress", {})
                    if pd and task_type in pd: seconds_done = pd[task_type].get("value", seconds_done)
                    await log(f"  [{name}] {seconds_done:.0f}/{seconds_needed}s", "progress")
                    if r.get("completed_at") or seconds_done >= seconds_needed: break
                elif r.get("error") == 429:
                    await asyncio.sleep(float(r.get("text", 5)) + 1)
                    continue
                await asyncio.sleep(HEARTBEAT_INTERVAL)
            await _api(s, hdrs, "POST", f"/quests/{qid}/heartbeat", payload={"stream_key": f"call:0:{pid}", "terminal": True})
            done.append(name)
            await log(f"✅ Completed: {name}", "ok")

        elif task_type == "PLAY_ACTIVITY":
            await log(f"🕹️ Activity: {name} ({seconds_done:.0f}/{seconds_needed}s)", "info")
            stream_key = "call:0:1"
            while seconds_done < seconds_needed:
                r = await _api(s, hdrs, "POST", f"/quests/{qid}/heartbeat", payload={"stream_key": stream_key, "terminal": False})
                if "error" not in r:
                    pd = r.get("progress", {})
                    if pd and "PLAY_ACTIVITY" in pd: seconds_done = pd["PLAY_ACTIVITY"].get("value", seconds_done)
                    await log(f"  [{name}] {seconds_done:.0f}/{seconds_needed}s", "progress")
                    if r.get("completed_at") or seconds_done >= seconds_needed: break
                elif r.get("error") == 429:
                    await asyncio.sleep(float(r.get("text", 5)) + 1)
                    continue
                await asyncio.sleep(HEARTBEAT_INTERVAL)
            await _api(s, hdrs, "POST", f"/quests/{qid}/heartbeat", payload={"stream_key": stream_key, "terminal": True})
            done.append(name)
            await log(f"✅ Completed: {name}", "ok")

    return done, errors", 5)) + 1)
                        continue
                if timestamp >= seconds_needed: break
                await asyncio.sleep(1)
            await _api(s, hdrs, "POST", f"/quests/{qid}/video-progress", payload={"timestamp": seconds_needed})
            done.append(name)
            await log(f"✅ Completed: {name}", "ok")

        elif task_type in ("PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP"):
            await log(f"🎮 {task_type}: {name} ({seconds_done:.0f}/{seconds_needed}s)", "info")
            pid = random.randint(1000, 30000)
            while seconds_done < seconds_needed:
                r = await _api(s, hdrs, "POST", f"/quests/{qid}/heartbeat", payload={"stream_key": f"call:0:{pid}", "terminal": False})
                if "error" not in r:
                    pd = r.get("progress", {})
                    if pd and task_type in pd: seconds_done = pd[task_type].get("value", seconds_done)
                    await log(f"  [{name}] {seconds_done:.0f}/{seconds_needed}s", "progress")
                    if r.get("completed_at") or seconds_done >= seconds_needed: break
                elif r.get("error") == 429:
                    await asyncio.sleep(float(r.get("text", 5)) + 1)
                    continue
                await asyncio.sleep(HEARTBEAT_INTERVAL)
            await _api(s, hdrs, "POST", f"/quests/{qid}/heartbeat", payload={"stream_key": f"call:0:{pid}", "terminal": True})
            done.append(name)
            await log(f"✅ Completed: {name}", "ok")

        elif task_type == "PLAY_ACTIVITY":
            await log(f"🕹️ Activity: {name} ({seconds_done:.0f}/{seconds_needed}s)", "info")
            stream_key = "call:0:1"
            while seconds_done < seconds_needed:
                r = await _api(s, hdrs, "POST", f"/quests/{qid}/heartbeat", payload={"stream_key": stream_key, "terminal": False})
                if "error" not in r:
                    pd = r.get("progress", {})
                    if pd and "PLAY_ACTIVITY" in pd: seconds_done = pd["PLAY_ACTIVITY"].get("value", seconds_done)
                    await log(f"  [{name}] {seconds_done:.0f}/{seconds_needed}s", "progress")
                    if r.get("completed_at") or seconds_done >= seconds_needed: break
                elif r.get("error") == 429:
                    await asyncio.sleep(float(r.get("text", 5)) + 1)
                    continue
                await asyncio.sleep(HEARTBEAT_INTERVAL)
            await _api(s, hdrs, "POST", f"/quests/{qid}/heartbeat", payload={"stream_key": stream_key, "terminal": True})
            done.append(name)
            await log(f"✅ Completed: {name}", "ok")

    return done, errors)

        elif task_type in ("PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP"):
            await log(f"🎮 {task_type}: {name} ({seconds_done:.0f}/{seconds_needed}s)", "info")
            pid = random.randint(1000, 30000)
            while seconds_done < seconds_needed:
                r = await _api(s, hdrs, "POST", f"/quests/{qid}/heartbeat", payload={"stream_key": f"call:0:{pid}", "terminal": False})
                if "error" not in r:
                    pd = r.get("progress", {})
                    if pd and task_type in pd: seconds_done = pd[task_type].get("value", seconds_done)
                    await log(f"  [{name}] {seconds_done:.0f}/{seconds_needed}s", "progress")
                    if r.get("completed_at") or seconds_done >= seconds_needed: break
                elif r.get("error") == 429:
                    await asyncio.sleep(float(r.get("text", 5)) + 1)
                    continue
                await asyncio.sleep(HEARTBEAT_INTERVAL)
            await _api(s, hdrs, "POST", f"/quests/{qid}/heartbeat", payload={"stream_key": f"call:0:{pid}", "terminal": True})
            done.append(name)
            await log(f"✅ Completed: {name}", "ok")

        elif task_type == "PLAY_ACTIVITY":
            await log(f"🕹️ Activity: {name} ({seconds_done:.0f}/{seconds_needed}s)", "info")
            stream_key = "call:0:1"
            while seconds_done < seconds_needed:
                r = await _api(s, hdrs, "POST", f"/quests/{qid}/heartbeat", payload={"stream_key": stream_key, "terminal": False})
                if "error" not in r:
                    pd = r.get("progress", {})
                    if pd and "PLAY_ACTIVITY" in pd: seconds_done = pd["PLAY_ACTIVITY"].get("value", seconds_done)
                    await log(f"  [{name}] {seconds_done:.0f}/{seconds_needed}s", "progress")
                    if r.get("completed_at") or seconds_done >= seconds_needed: break
                elif r.get("error") == 429:
                    await asyncio.sleep(float(r.get("text", 5)) + 1)
                    continue
                await asyncio.sleep(HEARTBEAT_INTERVAL)
            await _api(s, hdrs, "POST", f"/quests/{qid}/heartbeat", payload={"stream_key": stream_key, "terminal": True})
            done.append(name)
            await log(f"✅ Completed: {name}", "ok")

    return done, errorsesktop: {qname} ({seconds_done:.0f}/{seconds_needed}s)")
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
