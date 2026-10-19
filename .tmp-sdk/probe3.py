import asyncio, time
from openminion import Agent, tool
from openminion.api import APIRuntime
from pydantic import BaseModel

CFG = r".tmp-sdk/agents.json"
rt = APIRuntime.from_config_path(CFG, logging_mode="interactive")

@tool
def noop(x: str = "x") -> str:
    """Noop."""
    return x

async def main():
    ticks = 0
    stop = False
    async def heartbeat():
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0.05)
    hb = asyncio.create_task(heartbeat())
    t0 = time.perf_counter()
    agent = Agent(runtime=rt, tools=[noop], session_id="async-session")
    agent.run("inside a running loop")
    elapsed = time.perf_counter() - t0
    stop = True
    await hb
    print(f"Agent.run inside asyncio: {elapsed:.2f}s, heartbeat ticks during call={ticks} (a non-blocking call would yield ~{int(elapsed/0.05)} ticks)")

asyncio.run(main())

for tools in (["not.a.real.tool"], []):
    try:
        r = Agent(runtime=rt, tools=tools, session_id="t").run("hi")
        print(f"tools={tools!r}: ok reply={r.text!r}")
    except Exception as exc:
        print(f"tools={tools!r}: {type(exc).__name__}: {exc}")

class Out(BaseModel):
    value: int

try:
    Agent(runtime=rt, output_type=Out, session_id="o").run("hi")
except Exception as exc:
    print("pydantic on echo:", type(exc).__name__, str(exc)[:120])
