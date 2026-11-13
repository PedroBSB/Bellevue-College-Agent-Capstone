"""Built-in actions for plain-language requests.

Small local models can't reliably call tools, so bcagent recognizes common requests itself
("commit and push", "run the tests", "save it to colab", "open a pull request") and maps them to
its own slash commands. The user always confirms before an action runs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Tuple

COLAB_URL = re.compile(r"https?://colab\.research\.google\.com/drive/[\w-]+[^\s]*")

# (regex, slash command). Order doesn't matter: actions run in the order they appear in the request.
RULES: List[Tuple[str, str]] = [
    (r"\b(?:set ?up|create|add|configure|make)\b[^.?!]{0,30}\bssh\b|\bssh key\b", "/github ssh"),
    (r"\b(?:sign|log) ?in(?:to)?\b[^.?!]{0,20}\bgithub\b|\bgithub (?:login|sign ?in)\b", "/github login"),
    (r"\b(?:open|create|make|send|submit|start)\b[^.?!]{0,20}\b(?:pull request|pr)\b", "/github pr"),
    (r"\b(?:create|make|new|publish|put|upload)\b[^.?!]{0,25}\b(?:on|to)?\s*github\b(?! ?(?:pr|pull))"
     r"|\b(?:create|make)\b[^.?!]{0,15}\b(?:github )?repo(?:sitory)?\b", "/github create"),
    (r"\b(?:save|upload|push|sync|send)\b[^.?!]{0,25}\b(?:to|in|on)\s+(?:google\s+)?colab\b", "/colab push"),
    (r"\b(?:pull|refresh|reload|download|get)\b[^.?!]{0,25}\bfrom\s+(?:google\s+)?colab\b", "/colab pull"),
    (r"\b(?:revert|undo)\b[^.?!]{0,25}\bcommit\b", "/git revert"),
    (r"\bcommit\b(?![^.?!]{0,25}\b(?:message|history|log)\b)", "/git commit"),
    (r"\bpush\b(?=[^.?!]{0,30}\b(?:github|origin|remote|it|changes?|code|commits?|everything|branch)\b|\s*$)"
     r"|\bpush\b(?<=git push)", "/git push"),
    (r"\bpull\b(?=[^.?!]{0,30}\b(?:github|origin|remote|latest|changes|updates?)\b)|\bgit pull\b", "/git pull"),
    (r"\bgit status\b|\bwhat (?:files )?(?:have |has )?changed\b|\buncommitted\b", "/git"),
    (r"\b(?:run|execute|check)\b[^.?!]{0,15}\b(?:unit )?tests?\b|\bpytest\b", "/test"),
    (r"\breview\b[^.?!]{0,20}\b(?:my|the|this)\b[^.?!]{0,10}\bcode\b", "/review"),
]

QUESTION = re.compile(r"^\s*(?:explain|what|why|how|when|where|who|is|are|does|do|did|can i|should|tell me)\b")

FILLER = set("""a an the and then also please pls can could would you u me my mine our your it its this that these
those them all everything now just to on in into for of with up do go make sure i we let lets let's ok okay
changes change code files file work stuff git github repo repository branch current latest new
""".split())


@dataclass
class Plan:
    commands: List[str] = field(default_factory=list)
    action_only: bool = False   # the request is only about actions (no coding task left)
    rest: str = ""              # the request without the action words (for the agent)


def route(prompt: str) -> Plan:
    text = prompt.strip()
    low = text.lower()
    hits: List[Tuple[int, int, str]] = []
    m = COLAB_URL.search(text)
    if m:
        hits.append((m.start(), m.end(), "/colab " + m.group(0)))
    for pat, cmd in RULES:
        for mm in re.finditer(pat, low):
            hits.append((mm.start(), mm.end(), cmd))
    if not hits:
        return Plan(rest=text)
    hits.sort()
    commands: List[str] = []
    spans = []
    for start, end, cmd in hits:
        if cmd == "/git commit" and "/git revert" in commands:
            continue
        if cmd == "/git push" and any(c in commands for c in ("/colab push", "/github create")):
            continue
        if cmd not in commands:
            commands.append(cmd)
        spans.append((start, end))
    rest = text
    for start, end in sorted(spans, reverse=True):
        rest = rest[:start] + " " + rest[end:]
    words = [w for w in re.findall(r"[a-z0-9_.@/'-]+", rest.lower()) if w not in FILLER]
    rest = re.sub(r"(?:\s*\b(?:and|then|also|after that|afterwards)\b)+\s*$", "", " ".join(rest.split()),
                  flags=re.I).strip(" ,.;:-")
    action_only = len(words) <= 2 and not any(w.startswith("@") or "." in w for w in words)
    if QUESTION.match(low):  # "how do I push?" / "explain a commit" are questions for the agent
        commands = [c for c in commands if c.startswith("/colab http") or c == "/git"]
        action_only = action_only and commands == ["/git"]
    if not action_only:
        if commands == ["/review"]:
            commands = []  # "review the code for X" -> the agent handles it
        if not commands:
            return Plan(rest=" ".join(COLAB_URL.sub(" ", text).split()))
    return Plan(commands=commands, action_only=action_only, rest=rest if not action_only else "")
