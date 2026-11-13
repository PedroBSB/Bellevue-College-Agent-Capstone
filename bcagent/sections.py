"""Edit big Python files one function at a time.

Small models only see a few thousand words at once, so a long file can't be shown (or rewritten)
whole. When a request names functions or classes of a big file ("fix load_data in @big.py"), bcagent
shows the model only those definitions. The model answers with just the new versions, and bcagent
puts them back into the full file in the right place (`splice`). Everything else stays untouched.
"""
from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass
class Def:
    name: str
    start: int      # first line (1-based), including decorators
    end: int        # last line (1-based)
    col: int        # indentation of the `def` / `class` line
    top: bool       # defined at module level


def find_defs(source: str) -> List[Def]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    out: List[Def] = []

    def visit(node, top: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, DEF_TYPES):
                start = min([d.lineno for d in child.decorator_list] + [child.lineno])
                out.append(Def(child.name, start, child.end_lineno or child.lineno, child.col_offset, top))
                if isinstance(child, ast.ClassDef):
                    visit(child, False)
    visit(tree, True)
    return out


def select(source: str, prompt: str) -> List[Def]:
    """The definitions named in the prompt (whole classes win over their methods)."""
    words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", prompt))
    picked = [d for d in find_defs(source) if d.name in words]
    picked = [d for d in picked if not any(o is not d and o.start <= d.start and d.end <= o.end for o in picked)]
    return sorted(picked, key=lambda d: d.start)


def render(source: str, defs: List[Def]) -> str:
    """Only the chosen definitions, with markers for the hidden parts."""
    lines = source.splitlines()
    parts, prev = [], 0
    for d in defs:
        if d.start - 1 > prev:
            parts.append(f"# ... lines {prev + 1}-{d.start - 1} hidden ...")
        parts.append("\n".join(lines[d.start - 1:d.end]))
        prev = d.end
    if prev < len(lines):
        parts.append(f"# ... lines {prev + 1}-{len(lines)} hidden ...")
    return "\n\n".join(parts) + "\n"


def _segment(code_lines: List[str], node) -> str:
    start = min([d.lineno for d in node.decorator_list] + [node.lineno])
    return "\n".join(code_lines[start - 1:node.end_lineno])


def splice(source: str, new_code: str) -> Tuple[Optional[str], List[str], List[str], Optional[str]]:
    """Put the definitions from `new_code` into `source`.
    Returns (new source, replaced names, added names, error)."""
    code = textwrap.dedent(new_code.strip("\n"))
    code = "\n".join(l for l in code.splitlines() if not re.match(r"\s*# \.\.\. lines \d+-\d+ hidden \.\.\.", l))
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return None, [], [], f"the new code has a syntax error on line {e.lineno}: {e.msg}"
    code_lines = code.splitlines()
    old = find_defs(source)
    by_name: Dict[str, List[Def]] = {}
    for d in old:
        by_name.setdefault(d.name, []).append(d)
    lines = source.splitlines()
    replacements: List[Tuple[int, int, List[str]]] = []
    added: List[str] = []
    replaced: List[str] = []
    imports: List[str] = []
    for node in tree.body:
        if isinstance(node, DEF_TYPES):
            cands = by_name.get(node.name, [])
            tops = [c for c in cands if c.top]
            target = tops[0] if len(tops) == 1 else (cands[0] if len(cands) == 1 else None)
            seg = _segment(code_lines, node)
            if target:
                body = textwrap.indent(seg, " " * target.col) if target.col else seg
                replacements.append((target.start, target.end, body.splitlines()))
                replaced.append(node.name)
            else:
                added.append(seg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            stmt = "\n".join(code_lines[node.lineno - 1:node.end_lineno])
            if stmt.strip() not in {l.strip() for l in lines}:
                imports.append(stmt)
    if not replacements and not added:
        return None, [], [], "the answer contains no functions or classes to put into the file"
    for start, end, body in sorted(replacements, key=lambda r: r[0], reverse=True):
        lines[start - 1:end] = body
    if added:
        names = [re.match(r"\s*(?:@.*\n\s*)*(?:async\s+)?(?:def|class)\s+(\w+)", a) for a in added]
        main = next((i for i, l in enumerate(lines) if re.match(r"if __name__\s*==\s*['\"]__main__['\"]", l)), None)
        block = []
        for a in added:
            block += ["", ""] + a.splitlines()
        if main is None:
            while lines and not lines[-1].strip():
                lines.pop()
            lines += block
        else:
            lines[main:main] = block[2:] + ["", ""]
        added = [m.group(1) for m in names if m]
    if imports:
        last = 0
        for i, l in enumerate(lines[:200]):
            if re.match(r"(import|from)\s+\w", l):
                last = i + 1
        lines[last:last] = imports
    result = "\n".join(lines) + ("\n" if source.endswith("\n") else "")
    try:
        ast.parse(result)
    except SyntaxError as e:
        return None, [], [], f"the combined file would have a syntax error on line {e.lineno}: {e.msg}"
    return result, replaced, added, None
