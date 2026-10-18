"""Tools available to the agent.

Add your own tools here. Each tool is a plain function decorated with ``@tool``.
The agent will call them as needed and feed the results back into the model.
"""

from langchain_core.tools import tool


@tool
def add(a: float, b: float) -> float:
    """Add two numbers together and return the result."""
    return a + b


@tool
def multiply(a: float, b: float) -> float:
    """Multiply two numbers together and return the result."""
    return a * b


# The list of tools the agent can call. Replace or extend this with your own.
TOOLS = [add, multiply]
