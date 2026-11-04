import asyncio, json, base64, random, aiohttp

API = "https://discord.com/api/v9"
SUPPORTED = {"WATCH_VIDEO", "PLAY_ON_DESKTOP", "WATCH_VIDEO_ON_MOBILE", "PLAY_ACTIVITY"}

def make_headers(token: str):
    return {
        "Authorization": token,
        "Content-Type": "application/json",
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "discord/1.0.9189 Chrome/120.0.0.0 "
                       "Electron/28.2.10 Safari/537.36"),
        "X-Super-Properties": base64.b64encode(json.dumps({
            "os": "Windows", "browser": "Discord Client", "device": "",
            "system_locale": "en-US", "client_version": "1.0.9189",
            "release_channel": "stable", "client_build_number": 0,
        }).encode()).decode(),
    }

async def _api(session, hdrs, method, path, **kw):
    async with session.request(method, f"{API}{path}", headers=hdrs, **kw) as r:
        if r.status == 429:
            retry = (await r.json()).get("retry_after", 5)
            await asyncio.sleep(retry)
            return await _api(session, hdrs, method, path, **kw)
        if r.status >= 400:
            return None
        txt = await r.text()
        return json.loads(txt) if txt else {}

async def check_token(token: str):
    async with aiohttp.ClientSession() as s:
        return await _api(s, make_headers(token), "GET", "/users/@me")

async def run_pass(token: str, log=print):
    hdrs = make_headers(token)
    done = []
    async with aiohttp.ClientSession() as s:
        me = await _api(s, hdrs, "GET", "/users/@me")
        if not me:
            log("[!] token dead")
            return done
        log(f"[login] {me.get('username')}")

        quests = await _api(s, hdrs, "GET", "/quests/@me") or []
        for q in quests:
            if (q.get("user_status") or {}).get("completed_at"):
                continue
            cfg = q.get("config", {})
            task = cfg.get("task_config") or cfg.get("taskConfig") or {}
            ttype = (task.get("task_type") or task.get("taskType") or "").upper()
            if ttype not in SUPPORTED:
                continue
            qid = q["id"]
            await _api(s, hdrs, "POST", f"/quests/{qid}/enroll")
            await asyncio.sleep(random.uniform(0.8, 2.0))
            payload = {"quest_id": qid}
            if ttype == "WATCH_VIDEO":
                payload["stream_progress"] = 100
            else:
                payload["play_progress"] = 100
            await _api(s, hdrs, "POST", f"/quests/{qid}/progress", json=payload)
            await asyncio.sleep(random.uniform(1.0, 2.5))
            await _api(s, hdrs, "POST", f"/quests/{qid}/claim")
            done.append(q.get("name", qid))
            log(f"[done] {q.get('name', qid)}")
            await asyncio.sleep(random.uniform(2, 4))
    return done
