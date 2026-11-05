# mini_discord.py — minimal gateway client. NOT a discord.py replacement.
import asyncio, json, zlib, aiohttp

API = "https://discord.com/api/v9"
GATEWAY = "wss://gateway.discord.gg/?v=9&encoding=json"

class MiniClient:
    def __init__(self, token: str, on_message=None):
        self.token = token
        self.on_message = on_message
        self.session = None
        self.ws = None
        self.seq = None
        self.buffer = b""
        self.inflator = zlib.decompressobj()
        self.heartbeat_task = None
        self.user = None

    def headers(self):
        return {
            "Authorization": self.token,
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://example.com, 1.0)",
        }

    async def _identify(self):
        payload = {
            "op": 2,
            "d": {
                "token": self.token,
                "intents": 1 << 9,  # GUILD_MESSAGES
                "properties": {
                    "os": "linux", "browser": "mini", "device": "mini",
                },
            },
        }
        await self.ws.send_str(json.dumps(payload))

    async def _heartbeat(self, interval_ms: int):
        while True:
            await asyncio.sleep(interval_ms / 1000)
            if self.ws and not self.ws.closed:
                await self.ws.send_str(json.dumps({"op": 1, "d": self.seq}))

    async def _handle(self, msg: dict):
        op, t, d = msg.get("op"), msg.get("t"), msg.get("d")
        if msg.get("s") is not None:
            self.seq = msg["s"]

        if op == 10:  # HELLO
            interval = d["heartbeat_interval"]
            self.heartbeat_task = asyncio.create_task(self._heartbeat(interval))
            await self._identify()
        elif op == 11:  # HEARTBEAT ACK
            pass
        elif op == 7:  # RECONNECT
            await self.ws.close()
        elif t == "READY":
            self.user = d["user"]
            print(f"[ready] {self.user['username']}")
        elif t == "MESSAGE_CREATE":
            if self.on_message:
                asyncio.create_task(self.on_message(d))

    async def start(self):
        self.session = aiohttp.ClientSession()
        async with self.session.ws_connect(GATEWAY, heartbeat=None) as ws:
            self.ws = ws
            async for raw in ws:
                data = raw.data
                if raw.type == aiohttp.WSMsgType.TEXT:
                    await self._handle(json.loads(data))
                elif raw.type == aiohttp.WSMsgType.BINARY:
                    self.buffer += data
                    try:
                        out = self.inflator.decompress(self.buffer)
                        self.buffer = b""
                        await self._handle(json.loads(out))
                    except Exception:
                        continue
                elif raw.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        if self.heartbeat_task:
            self.heartbeat_task.cancel()

    async def send(self, channel_id: str, content: str):
        async with self.session.post(
            f"{API}/channels/{channel_id}/messages",
            headers=self.headers(),
            json={"content": content},
        ) as r:
            return await r.json()

    async def close(self):
        if self.ws:
            await self.ws.close()
        if self.session:
            await self.session.close()
