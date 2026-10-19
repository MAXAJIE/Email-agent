from openminion import Agent, ProviderError, tool


@tool
def lookup_order(order_id: str) -> str:
    """Return the current status for one order."""

    return f"order {order_id}: ready"


try:
    with Agent(
        instructions="Use lookup_order and answer in one sentence.",
        tools=[lookup_order],
        forced_tools=["lookup_order"],
        session_id="hackathon-demo",
    ) as agent:
        result = agent.run("Check order A-104")
        print(result.text)
        print(f"session={result.session_id}")
except ProviderError as exc:
    print(f"provider failed: {exc.code}: {exc.message}")