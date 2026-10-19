import threading, time
from openminion import Agent, tool
from openminion.api import APIRuntime

CFG = r".tmp-sdk/agents.json"
rt = APIRuntime.from_config_path(CFG, logging_mode="interactive")

@tool
def alpha(x: str = "a") -> str:
    """Alpha tool."""
    return x

@tool
def beta(x: str = "b") -> str:
    """Beta tool."""
    return x

a1 = Agent(runtime=rt, tools=[alpha], session_id="s1")
a2 = Agent(runtime=rt, tools=[beta], session_id="s2")

t0 = time.perf_counter()
a1.run("one")
t1 = time.perf_counter()
a2.run("two")
t2 = time.perf_counter()
print(f"sequential two agents: {t2-t0:.2f}s (first {t1-t0:.2f}, second {t2-t1:.2f})")

results = {}
def work(i):
    t = time.perf_counter()
    Agent(runtime=rt, tools=[alpha], session_id=f"p{i}").run(f"msg{i}")
    results[i] = time.perf_counter() - t

t0 = time.perf_counter()
threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
[t.start() for t in threads]
[t.join() for t in threads]
total = time.perf_counter() - t0
print(f"4 parallel runs shared runtime: total {total:.2f}s; per-run {[round(v,2) for v in results.values()]}")
