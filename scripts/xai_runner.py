#!/usr/bin/env python3
"""Optional isolated text-evaluation runner. Requires a securely configured XAI_API_KEY.

This runner does NOT emulate tool-using development. Use a project sandbox runner to
evaluate tool behavior. There are no retries and no secrets in argv or output.
"""
import json
import os
import sys
import urllib.error
import urllib.request


class RunnerError(ValueError):
    """Only messages deliberately authored here are safe to display."""


def main():
    key = os.environ.get("XAI_API_KEY")
    if not key:
        raise RunnerError("XAI_API_KEY is not configured in the secure process environment")
    data = json.load(sys.stdin)
    payload = {"model": data["model_id"], "messages": [
        {"role": "system", "content": data["instruction"]}, {"role": "user", "content": data["input"]}],
        "max_tokens": 1024}
    request = urllib.request.Request("https://api.x.ai/v1/chat/completions",
                                     data=json.dumps(payload).encode(), method="POST",
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    # Reject redirects: never send credentials to an endpoint selected by an HTTP redirect.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=25) as response:
            raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise RunnerError("API response too large")
            result = json.loads(raw)
    except urllib.error.HTTPError as exc:
        raise RunnerError("Evaluation API returned HTTP %s; no automatic retry" % exc.code)
    answer = result["choices"][0]["message"]["content"]
    if not isinstance(answer, str):
        raise RunnerError("API returned no answer text")
    print(json.dumps({"answer": answer, "usage": result.get("usage", {})}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Do not print provider responses, headers, raw request or tokens.
        print(json.dumps({"error": str(exc) if isinstance(exc, RunnerError) else type(exc).__name__}), file=sys.stderr)
        raise SystemExit(2)
