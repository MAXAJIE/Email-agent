import time, traceback
from openminion import Agent, tool
from openminion.api import APIRuntime

CFG = r".tmp-sdk/agents.json"
rt = APIRuntime.from_config_path(CFG, logging_mode="interactive")
print("tool count:", len(rt.tools.list()))
print("sample tools:", sorted(rt.tools.list())[:15])

@tool
def add(left: int, right: int) -> int:
    """Add two numbers."""
    return left + right

print("before:", "add" in rt.tools.list())
a = Agent(runtime=rt, tools=[add], session_id="probe-session")
r = a.run("hello")
print("reply:", repr(r.text))
print("ids:", r.id, r.run_id, r.run_state, r.session_id)
print("after run, 'add' registered:", "add" in rt.tools.list())
print("metadata keys:", sorted(r.metadata)[:20])
print("stats:", r.stats)
