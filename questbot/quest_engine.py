import asyncio, json, base64, random, aiohttp

API = "https://discord.com/api/v9"
SUPPORTED = {"WATCH_VIDEO", "PLAY_ON_DESKTOP", "WATCH_VIDEO_ON_MOBILE", "PLAY_ACTIVITY"}
_session = None
_session_lock = asyncio.Lock()

def make_headers(token: str):
    return {
        "Authorization": token,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9189 Chrome/120.0.0.0 Electron/28.2.10 Safari/537.36",
        "X-Super-Properties": base64.b64encode(json.dumps({
            "os": "Windows", "browser": "Discord Client", "device": "",
            "system_locale": "en-US", "client_version": "1.0.9189",
            "release_channel": "stable", "client_build_number": 0,
        }).encode()).decode(),
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

async def run_pass(token: str):
    hdrs = make_headers(token)
    done, errors = [], []
    me = await _api(hdrs, "GET", "/users/@me")
    if "error" in me:
        return done, ["Token invalid or network error."]

    quests = await _api(hdrs, "GET", "/quests/@me")
    if not isinstance(quests, list):
        err = quests.get("message", "Bad quest response") if isinstance(quests, dict) else "Bad quest response"
        return done, [err]

    for q in quests:
        if not isinstance(q, dict): continue
        if (q.get("user_status") or {}).get("completed_at"): continue
        cfg = q.get("config", {})
        task = cfg.get("task_config") or cfg.get("taskConfig") or {}
        ttype = (task.get("task_type") or task.get("taskType") or "").upper()
        if ttype not in SUPPORTED: continue
        qid = q.get("id")
        if not qid: continue

        await _api(hdrs, "POST", f"/quests/{qid}/enroll")
        await asyncio.sleep(random.uniform(0.8, 2.0))
        payload = {"quest_id": qid}
        payload["stream_progress" if ttype == "WATCH_VIDEO" else "play_progress"] = 100
        await _api(hdrs, "POST", f"/quests/{qid}/progress", json=payload)
        await asyncio.sleep(random.uniform(1.0, 2.5))
        claim = await _api(hdrs, "POST", f"/quests/{qid}/claim")
        if "error" in claim:
            errors.append(f"Failed to claim {q.get('name', qid)}")
        else:
            done.append(q.get("name", qid))
        await asyncio.sleep(random.uniform(2, 4))
    return done, errors
