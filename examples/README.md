# Examples

| File | What it is | Use it with |
|------|------------|-------------|
| `course-intro-python.md` | course profile for an intro course: hints instead of solutions, session logs | copy to `<assignment>/.bcagent/course.md`, or `bcagent course install course-intro-python.md` |
| `course-exam-day.md` | course profile that turns AI help off during an exam window | same (edit `exam_start` / `exam_end`) |
| `course-data-science.md` | course profile for a statistics course with its own agent | same, plus `bcagent agents install statsdog.md` |
| `statsdog.md` | a custom agent: statistics helper using pandas, statsmodels and statsformula | `bcagent agents install statsdog.md` then `bcagent -a statsdog` |
| `grades_server.py` | a 10-line MCP server that gives the agent a `get_grade` tool | `pip install mcp`, then `bcagent mcp add grades -- python grades_server.py` |

See the main [README](../README.md) for explanations.
