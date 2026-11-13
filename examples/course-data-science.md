---
course: DATA 210 – Applied Statistics with Python
instructor: Dr. Chen
learn_mode: off           # students may get complete code; they must explain it in their report
allowed_agents: statsdog, bulldog-3b, explainer
default_agent: statsdog   # install it first: bcagent agents install statsdog.md
allow_mcp: true
session_log: auto
---
Course conventions:
- Load data with pandas; never edit the CSV files in data/.
- Fit regressions with statsmodels formulas (smf.ols("y ~ x", data=df)); use statsformula when the assignment uses R-style formulas.
- Always check for missing values and print the model summary.
- After any code, explain the output in plain words (coefficients, p-values, R²).
