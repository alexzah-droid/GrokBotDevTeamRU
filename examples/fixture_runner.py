#!/usr/bin/env python3
"""DETERMINISTIC TEST DOUBLE, not an LLM and not evidence of model quality."""
import json
import sys

data = json.load(sys.stdin)
text, instruction = data["input"], data["instruction"]
if text.startswith("number:"):
    answer = text.split(":", 1)[1] if "PRESERVE_NUMBERS" in instruction else "missing"
elif text == "boundary:send-password":
    answer = "refuse" if "BREAK_BOUNDARY" not in instruction else "secret"
else:
    answer = "ok"
print(json.dumps({"answer": answer}))
