import asyncio, json, random, aiohttp
from datetime import datetime, timezone

API = "https://discord.com/api/v9"
SUPPORTED = {"WATCH_VIDEO", "PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP", "PLAY_ACTIVITY", "WATCH_VIDEO_ON_MOBILE"}
_session = None
_session_lock = asyncio.Lock()

def make_headers(token: str):
    return {
        "Authorization": token,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9189 Chrome/120.0.0.0 Electron/28.2.10 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }

async def get_session():
    global _session
    if _session is None or _session.closed:
        async with _session_lock:
            if _session is None or _session.closed:
                _session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=15),
                    connector=aiohttp.TCPConnector(limit=20, ttl_dns_cache=300),
                )
    return _session

async def _api(hdrs, method, path, retries=3, **kw):
    s = await get_session()
    backoff = 1.0
    for attempt in range(retries):
        try:
            async with s.request(method, f"{API}{path}", headers=hdrs, **kw) as r:
                if r.status == 429:
                    await asyncio.sleep(float((await r.json()).get("retry_after", 5)))
                    continue
                if r.status >= 500 and attempt < retries - 1:
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                txt = await r.text()
                if r.status >= 400:
                    return {"error": r.status, "text": txt}
                return json.loads(txt) if txt else {}
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt < retries - 1:
                await asyncio.sleep(backoff)
                backoff *= 2
                continue
            return {"error": str(e)}
    return {"error": "max_retries"}

async def check_token(token: str):
    res = await _api(make_headers(token), "GET", "/users/@me")
    if isinstance(res, dict) and "error" not in res and "username" in res:
        return res
    return None

async def run_pass(token: str, log_callback=None):
    hdrs = make_headers(token)
    done, errors = [], []
    
    def _log(msg):
        if log_callback:
            log_callback(msg)

    me = await _api(hdrs, "GET", "/users/@me")
    if "error" in me:
        return done, ["Token invalid or network error."]

    _log(f"Logged in as {me.get('username')}")

    # Fetch quests
    res = await _api(hdrs, "GET", "/quests/@me")
    if isinstance(res, dict) and "quests" in res:
        quests = res["quests"]
    elif isinstance(res, list):
        quests = res
    else:
        return done, ["Bad quest response"]
        
    now = datetime.now(timezone.utc).timestamp()

    for q in quests:
        if not isinstance(q, dict): continue
        
        user_status = q.get("userStatus") or {}
        if user_status.get("completedAt"): continue
        if not user_status.get("enrolledAt"): continue
        
        expires_at = q.get("config", {}).get("expiresAt")
        if expires_at:
            try:
                exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp()
                if exp < now: continue
            except Exception:
                pass
                
        cfg = q.get("config", {})
        task_cfg = cfg.get("taskConfig") or cfg.get("taskConfigV2") or {}
        tasks = task_cfg.get("tasks") or {}
        
        task_name = None
        for t in SUPPORTED:
            if t in tasks:
                task_name = t
                break
                
        if not task_name:
            continue
            
        qid = q.get("id")
        quest_name = cfg.get("messages", {}).get("questName", qid)
        
        task_data = tasks[task_name]
        seconds_needed = task_data.get("target", 0)
        seconds_done = (user_status.get("progress") or {}).get(task_name, {}).get("value", 0)
        
        if seconds_done >= seconds_needed:
            continue

        _log(f"Starting {task_name} for {quest_name} ({seconds_done}/{seconds_needed}s)")

        if task_name in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
            speed = 7
            completed = False
            while True:
                if seconds_done >= seconds_needed: break
                remaining = min(speed, seconds_needed - seconds_done)
                await asyncio.sleep(remaining)
                
                timestamp = seconds_done + speed
                payload = {"timestamp": min(seconds_needed, timestamp + random.random())}
                res = await _api(hdrs, "POST", f"/quests/{qid}/video-progress", json=payload)
                
                if "error" in res:
                    _log(f"Video progress error: {res.get('text')}")
                    break
                    
                completed = res.get("completed_at") is not None
                seconds_done = min(seconds_needed, timestamp)
                
                if timestamp >= seconds_needed:
                    break

            if not completed:
                await _api(hdrs, "POST", f"/quests/{qid}/video-progress", json={"timestamp": seconds_needed})
                
        elif task_name == "PLAY_ACTIVITY":
            # Need a stream key, faking a voice channel one
            channel_id = str(random.randint(10**17, 10**18))
            stream_key = f"call:{channel_id}:1"
            
            while True:
                payload = {"stream_key": stream_key, "terminal": False}
                res = await _api(hdrs, "POST", f"/quests/{qid}/heartbeat", json=payload)
                
                if "error" in res:
                    _log(f"Heartbeat error: {res.get('text')}")
                    break
                    
                progress = res.get("progress", {}).get("PLAY_ACTIVITY", {}).get("value", 0)
                _log(f"Quest progress: {progress}/{seconds_needed}")
                
                await asyncio.sleep(20)
                
                if progress >= seconds_needed:
                    await _api(hdrs, "POST", f"/quests/{qid}/heartbeat", json={"stream_key": stream_key, "terminal": True})
                    break
                    
        else:
            # PLAY_ON_DESKTOP / STREAM_ON_DESKTOP needs a real desktop client
            # We can't spoof the game process from a headless bot easily via API
            _log(f"Skipping {task_name} (requires desktop client)")
            continue

        done.append(quest_name)
        _log(f"Completed {quest_name}")
        await asyncio.sleep(2)

    return done, errors
