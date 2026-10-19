import inspect, time
from openminion import tool
from openminion.modules.tool.framework import derive_tool_specs
from openminion.api import APIRuntime

@tool
async def async_lookup(order_id: str) -> str:
    """Look up an order asynchronously."""
    return order_id

family = async_lookup.tool_family_spec()
spec = derive_tool_specs(family)[0]
result = spec.handler(spec.args_model(order_id="A-1"), None)
print("async tool handler returns:", type(result).__name__, "coroutine?", inspect.iscoroutine(result))
result.close()

t0 = time.perf_counter()
rt = APIRuntime.from_config_path(r".tmp-sdk/agents.json", logging_mode="interactive")
t1 = time.perf_counter()
print(f"APIRuntime.from_config_path cold start: {t1-t0:.2f}s, tools={len(rt.tools.list())}")
rt.close()
