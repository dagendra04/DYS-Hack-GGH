"""Two-person room signaling. Relays WebRTC offers/answers/ICE and small JSON messages. Stores nothing."""
import logging
import re
import secrets

log = logging.getLogger("bhashabridge")

ROOM_RE = re.compile(r"^[A-Z0-9]{4,12}$")
ALLOWED = {"offer", "answer", "ice", "profile", "utterance"}
MAX_MSG = 20_000


class RoomManager:
    def __init__(self):
        self.rooms: dict = {}

    async def _safe(self, ws, data):
        try:
            await ws.send_json(data)
        except Exception as e:
            log.warning("signaling send failed: %s", type(e).__name__)

    async def join(self, code: str, ws):
        room = self.rooms.setdefault(code, {})
        if len(room) >= 2:
            return None
        pid = secrets.token_hex(4)
        others = list(room.values())
        room[pid] = ws
        await ws.send_json({"type": "joined", "peer_id": pid, "peers": len(others)})
        for o in others:
            await self._safe(o, {"type": "peer_joined", "peer_id": pid})
        return pid

    async def relay(self, code: str, pid: str, msg: dict):
        if msg.get("type") not in ALLOWED:
            return
        if msg["type"] == "utterance":
            for k in ("original", "translated"):
                if not isinstance(msg.get(k), str) or len(msg[k]) > 1000:
                    return
        for other_id, ws in list(self.rooms.get(code, {}).items()):
            if other_id != pid:
                await self._safe(ws, {**msg, "from": pid})

    async def leave(self, code: str, pid: str):
        room = self.rooms.get(code)
        if not room:
            return
        room.pop(pid, None)
        for ws in list(room.values()):
            await self._safe(ws, {"type": "peer_left", "peer_id": pid})
        if not room:
            self.rooms.pop(code, None)


rooms = RoomManager()
