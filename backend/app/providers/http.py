import asyncio

import httpx

from .errors import network


async def post(url: str, *, timeout: float = 30, **kw) -> httpx.Response:
    """POST with one automatic retry on 429/503 (short wait, honours Retry-After up to 3 s)."""
    r = None
    for attempt in (0, 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.post(url, **kw)
        except httpx.HTTPError:
            raise network()
        if r.status_code in (429, 503) and attempt == 0:
            try:
                wait = min(float(r.headers.get("retry-after", "1.5")), 3.0)
            except ValueError:
                wait = 1.5
            await asyncio.sleep(wait)
            continue
        break
    return r
