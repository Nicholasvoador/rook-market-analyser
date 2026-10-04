"""Tiny CDP driver for headless UI checks: navigate, collect console errors, eval, screenshot."""
import asyncio
import base64
import json
import os
import sys
import urllib.request

import websockets

PORT = int(os.environ.get("CDP_PORT", "9333"))


class CDP:
    def __init__(self, ws):
        self.ws, self.n, self.pending, self.events = ws, 0, {}, []

    async def reader(self):
        async for raw in self.ws:
            m = json.loads(raw)
            if "id" in m and m["id"] in self.pending:
                self.pending.pop(m["id"]).set_result(m)
            else:
                self.events.append(m)

    async def call(self, method, **params):
        self.n += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.n] = fut
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        r = await asyncio.wait_for(fut, 60)
        if "error" in r:
            raise RuntimeError(r["error"])
        return r.get("result", {})

    async def eval(self, expr):
        r = await self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    def errors(self):
        out = []
        for e in self.events:
            if e.get("method") == "Runtime.exceptionThrown":
                d = e["params"]["exceptionDetails"]
                out.append("EXC " + (d.get("exception", {}).get("description") or d.get("text", ""))[:300])
            elif e.get("method") == "Runtime.consoleAPICalled" and e["params"]["type"] in ("error", "warning"):
                out.append(e["params"]["type"].upper() + " " + " ".join(
                    str(a.get("value", a.get("description", "")))[:200] for a in e["params"]["args"]))
            elif e.get("method") == "Log.entryAdded" and e["params"]["entry"]["level"] in ("error",):
                out.append("LOG " + e["params"]["entry"]["text"][:200] + " " + e["params"]["entry"].get("url", "")[:100])
        return out


async def session(steps):
    tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
    page = next(t for t in tabs if t["type"] == "page")
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=50_000_000) as ws:
        c = CDP(ws)
        task = asyncio.create_task(c.reader())
        for m in ("Runtime.enable", "Log.enable", "Page.enable"):
            await c.call(m)
        await c.call("Emulation.setDeviceMetricsOverride", width=1680, height=1050, deviceScaleFactor=1, mobile=False)
        await steps(c)
        task.cancel()


async def shot(c, path, full=False):
    params = {"format": "png"}
    if full:
        h = await c.eval("document.querySelector('.main').scrollHeight + 60")
        await c.call("Emulation.setDeviceMetricsOverride", width=1680, height=int(h), deviceScaleFactor=1, mobile=False)
        await c.eval("document.querySelector('.main').style.overflow='visible'; document.body.style.overflow='visible'; "
                     "document.querySelector('.shell').style.height='auto'")
        await asyncio.sleep(1.5)
    r = await c.call("Page.captureScreenshot", **params)
    open(path, "wb").write(base64.b64decode(r["data"]))
    if full:
        await c.call("Emulation.setDeviceMetricsOverride", width=1680, height=1050, deviceScaleFactor=1, mobile=False)


if __name__ == "__main__":
    print("import me")
    sys.exit(0)
