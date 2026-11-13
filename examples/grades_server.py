"""A tiny MCP server: gives the agent a tool to look up course grades."""
from mcp.server import MCPServer

mcp = MCPServer("grades")

GRADES = {"alice": 93, "bob": 78, "chen": 88}


@mcp.tool()
def get_grade(student: str) -> str:
    """Return the course grade (0-100) of a student by first name."""
    grade = GRADES.get(student.lower())
    return f"{student}: {grade}" if grade is not None else f"no student named {student}"


if __name__ == "__main__":
    mcp.run()
