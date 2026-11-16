"""Optional second look at "free" issues with Claude or ChatGPT.

The rules decide what's free from claims, PRs and labels. People say "I'm on it" in more ways
than any rule can list, so when an Anthropic API key is set, every issue the rules call free
is read once more by a small model: description, comments, linked PRs and age. It answers
free, taken or not_ready with a one-line reason. Anything it doesn't call free moves to its own
board section with that reason, so nothing disappears unseen.

The model comes from Anthropic (ANTHROPIC_API_KEY) or OpenAI (OPENAI_API_KEY). `ai.provider` picks
one; left out, Anthropic is used when its key is set, otherwise OpenAI. The GitHub reading is the
same either way: the model only judges what scout fetched.

Verdicts are cached per issue until the issue changes, and each run makes at most
`ai.max_calls` calls, so after the first run it's a handful a day. Without a key nothing changes.
"""

import json
import os
import urllib.error
import urllib.request

from .classify import days_between
from .github import GitHubError

API = "https://api.anthropic.com/v1/messages"
OPENAI_API = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "claude-haiku-5-5"
OPENAI_DEFAULT_MODEL = "gpt-5-mini"
PROVIDERS = {"anthropic": ("ANTHROPIC_API_KEY", DEFAULT_MODEL), "openai": ("OPENAI_API_KEY", OPENAI_DEFAULT_MODEL)}
VERDICTS = ("free", "taken", "not_ready")
BODY_CHARS = 6000
COMMENT_CHARS = 800
MAX_COMMENTS = 25
MAX_TOKENS = 1024          # the answer is tiny; the cap only needs room for a model that says a bit more
OPENAI_MAX_TOKENS = 4000   # OpenAI's reasoning models count their thinking against the same cap

SYSTEM = """You help an open-source contributor decide which GitHub issues are worth starting.
Rules have already found no open PR, no obvious claim and no blocking label. Read the issue and
answer with exactly one JSON object and nothing else:
{"verdict": "free" | "taken" | "not_ready", "reason": "<at most 20 words>"}

taken: someone owns it. The author or a commenter says they are building it, has a draft, branch,
prototype or closed-but-resumable PR, or is actively iterating on the design.
not_ready: nobody should start coding yet. It needs a maintainer decision or design agreement,
depends on other unfinished work, is an umbrella or tracking issue, or looks obsolete.
free: a contributor could reasonably comment "I'd like to work on this" today.
Judge from the text only; when unsure between free and another verdict, say why in the reason."""


class AIError(RuntimeError):
    pass


def _post(url, headers, payload, timeout=60):
    name = "OpenAI" if "openai.com" in url else "Anthropic"
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as err:
        body = " ".join(err.read().decode("utf-8", "replace").split())[:200]
        raise AIError(f"HTTP {err.code} from the {name} API: {body}") from None
    except urllib.error.URLError as err:
        raise AIError(f"network error reaching the {name} API: {err.reason}") from None


class Judge:
    def __init__(self, api_key, model=DEFAULT_MODEL, max_calls=40, post=_post, max_tokens=MAX_TOKENS,
                 provider="anthropic"):
        if provider not in PROVIDERS:
            raise AIError(f"unknown ai.provider {provider!r}; use one of {', '.join(PROVIDERS)}")
        self.api_key = api_key
        self.provider = provider
        self.model = model
        self.max_calls = max_calls
        self.max_tokens = max_tokens
        self.calls = 0
        self._post = post

    @classmethod
    def from_env(cls, cfg):
        ai = cfg.get("ai") or {}
        if ai.get("enabled") is False:
            return None
        provider = ai.get("provider")
        if provider is None:                  # whichever key is set, Anthropic first
            provider = next((p for p, (env, _) in PROVIDERS.items() if os.environ.get(env)), None)
            if provider is None:
                return None
        if provider not in PROVIDERS:
            raise AIError(f"unknown ai.provider {provider!r}; use one of {', '.join(PROVIDERS)}")
        env, default_model = PROVIDERS[provider]
        key = os.environ.get(env)
        if not key:
            # The chosen provider has no key: use the other one rather than switch the check off.
            other = next((p for p, (e, _) in PROVIDERS.items() if p != provider and os.environ.get(e)), None)
            if other is None:
                return None
            provider, (env, default_model) = other, PROVIDERS[other]
            key = os.environ[env]
            ai = {k: v for k, v in ai.items() if k not in ("model", "max_tokens")}   # meant for the first choice
        cap = ai.get("max_tokens", OPENAI_MAX_TOKENS if provider == "openai" else MAX_TOKENS)
        return cls(key, ai.get("model", default_model), ai.get("max_calls", 40),
                   max_tokens=cap, provider=provider)

    def _complete(self, system, prompt, model, max_tokens):
        """One call; returns (text, stop) with stop as "max_tokens", "refusal" or something else."""
        if self.provider == "openai":
            # Reasoning models spend part of the budget thinking, so the cap is the total, not the answer.
            data = self._post(OPENAI_API, {"authorization": f"Bearer {self.api_key}",
                                           "content-type": "application/json"},
                              {"model": model, "max_completion_tokens": max_tokens,
                               "response_format": {"type": "json_object"},
                               "messages": [{"role": "system", "content": system},
                                            {"role": "user", "content": prompt}]})
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            if message.get("refusal"):
                return "", "refusal"
            finish = choice.get("finish_reason")
            stop = {"length": "max_tokens", "content_filter": "refusal"}.get(finish, finish)
            return message.get("content") or "", stop
        data = self._post(API, {"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                                "content-type": "application/json"},
                          {"model": model, "max_tokens": max_tokens, "system": system,
                           "messages": [{"role": "user", "content": prompt}]})
        text = "".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
        return text, data.get("stop_reason")

    def ask(self, prompt):
        self.calls += 1
        text, stop = self._complete(SYSTEM, prompt, self.model, self.max_tokens)
        if stop == "refusal":
            raise AIError("model stopped with refusal")
        if stop != "max_tokens":
            return parse_verdict(text)
        try:                                  # cut off, but the JSON may already be complete
            return parse_verdict(text)
        except AIError:
            start = " ".join(text.split())[:120]
            raise AIError(f"model stopped with max_tokens ({self.max_tokens}); reply began: {start!r}") from None

    def ask_json(self, system, prompt, model=None, max_tokens=None):
        """One call with another system prompt; returns the answer's text for the caller to parse.
        Counts against the same call budget as ask(). A model name meant for the other provider
        (say a Claude model while ChatGPT is configured) falls back to this judge's model."""
        self.calls += 1
        if model and (model.startswith("claude") != (self.provider == "anthropic")):
            model = None
        text, stop = self._complete(system, prompt, model or self.model, max_tokens or self.max_tokens)
        if stop == "refusal":
            raise AIError("model stopped with refusal")
        return text


def parse_verdict(text):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise AIError("no JSON in the model's answer")
    try:
        out = json.loads(text[start:end + 1])
    except ValueError:
        raise AIError("unreadable JSON in the model's answer") from None
    verdict = str(out.get("verdict", "")).strip().lower()
    if verdict not in VERDICTS:
        raise AIError(f"unknown verdict {verdict!r}")
    reason = " ".join(str(out.get("reason", "")).split())[:160]
    return verdict, reason


def build_prompt(cand, events, now):
    lines = [f"Today: {now.strftime('%Y-%m-%d')}",
             f"Issue: {cand.repo}#{cand.number} {cand.title}",
             f"Author: {cand.author}"]
    if cand.created_at:
        lines.append(f"Opened {days_between(cand.created_at, now)} days ago")
    if cand.labels:
        lines.append("Labels: " + ", ".join(cand.labels))
    prs = []
    for ev in events:
        if ev.get("event") != "cross-referenced":
            continue
        src = (ev.get("source") or {}).get("issue") or {}
        if "pull_request" in src:
            merged = (src.get("pull_request") or {}).get("merged_at")
            state = "merged" if merged else src.get("state", "?")
            prs.append(f"#{src.get('number')} ({state}, by {(src.get('user') or {}).get('login', '?')})")
    if prs:
        lines.append("Pull requests that mention it: " + ", ".join(prs))
    lines += ["", "Description:", (cand.body or "(empty)")[:BODY_CHARS], ""]
    comments = [ev for ev in events if ev.get("event") == "commented"]
    for ev in comments[-MAX_COMMENTS:]:
        who = (ev.get("actor") or ev.get("user") or {}).get("login", "?")
        when = ev.get("created_at", "")
        age = f"{days_between(when, now)}d ago" if when else "?"
        lines.append(f"--- comment by {who}, {age}:")
        lines.append((ev.get("body") or "")[:COMMENT_CHARS])
    if not comments:
        lines.append("(no comments)")
    return "\n".join(lines)


def review_free(judge, gh, cands, now, cache, errors):
    """Second look at the rules' free candidates. Returns (still_free, set_aside, new_cache), where
    set_aside is [(cand, verdict, reason)]. Unjudged candidates (no budget left, or an error) stay free."""
    still, aside, new_cache = [], [], {}
    failed = False
    for cand in cands:
        hit = cache.get(cand.key)
        if hit and hit.get("updated_at") == cand.updated_at:
            verdict, reason = hit["verdict"], hit.get("reason", "")
        elif not failed and judge.calls < judge.max_calls:
            try:
                events = gh.timeline(cand.repo, cand.number)
                verdict, reason = judge.ask(build_prompt(cand, events, now))
            except (AIError, GitHubError) as err:
                errors.append(f"AI check of {cand.key}: {err}")
                failed = isinstance(err, AIError) and "HTTP 4" in str(err)   # bad key or quota: stop asking
                still.append(cand)
                continue
        else:
            still.append(cand)                # judged on a later run
            continue
        new_cache[cand.key] = {"updated_at": cand.updated_at, "verdict": verdict, "reason": reason}
        if verdict == "free":
            still.append(cand)
        else:
            aside.append((cand, verdict, reason))
    return still, aside, new_cache

