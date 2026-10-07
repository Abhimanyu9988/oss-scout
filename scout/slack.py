"""Format a Digest as Slack Block Kit and post it through an incoming webhook."""

import json
import urllib.error
import urllib.request

MAX_LINES_PER_SECTION = 10
SECTION_CHAR_LIMIT = 2900  # Slack caps a section's text at 3000 characters


def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _line(item):
    ref = item.key.split("/")[-1]  # "opentelemetry-collector-contrib#123"
    number = "#" + ref.split("#")[-1]
    title = item.title if len(item.title) <= 90 else item.title[:87] + "..."
    detail = f" — {esc(item.detail)}" if item.detail else ""
    return f"• <{item.url}|{number}> {esc(title)}{detail}"


def _section(heading, items):
    lines = [_line(i) for i in items[:MAX_LINES_PER_SECTION]]
    if len(items) > MAX_LINES_PER_SECTION:
        lines.append(f"_…and {len(items) - MAX_LINES_PER_SECTION} more_")
    text = f"*{heading}*\n" + "\n".join(lines)
    if len(text) > SECTION_CHAR_LIMIT:
        text = text[:SECTION_CHAR_LIMIT].rsplit("\n", 1)[0] + "\n_…truncated_"
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def build_payload(digest, title, date_label):
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": f"{title} · {date_label}"}}]
    if digest.baseline:
        c = digest.counts
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": (
            "*Watching from today.* From tomorrow you'll only hear about changes.\n"
            f"{c.get('free', 0)} free · {c.get('quiet', 0)} claimed-but-quiet · "
            f"{c.get('blocked', 0)} blocked · {c.get('taken', 0)} taken")}})
    if digest.needs_you:
        blocks.append(_section("Needs you", digest.needs_you))
    if digest.to_pick:
        heading = "Most recent free issues" if digest.baseline else "New to pick up"
        blocks.append(_section(heading, digest.to_pick))
    if digest.review_queue:
        blocks.append(_section("Waiting for a first review", digest.review_queue))
    if digest.errors:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": esc(
            f"Partial scan, {len(digest.errors)} error(s). First: {digest.errors[0][:200]}")}]})
    total = len(digest.needs_you) + len(digest.to_pick) + len(digest.review_queue)
    fallback = f"{title}: {total} item(s) need a look"
    return {"text": fallback, "blocks": blocks}


def to_plain_text(payload):
    """Readable rendering for --dry-run and the Actions job summary."""
    out = []
    for block in payload["blocks"]:
        if block["type"] == "header":
            out.append(f"# {block['text']['text']}")
        elif block["type"] == "section":
            out.append(block["text"]["text"])
        elif block["type"] == "context":
            out.append(block["elements"][0]["text"])
        out.append("")
    return "\n".join(out).strip() + "\n"


def post(webhook_url, payload):
    req = urllib.request.Request(webhook_url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"Slack webhook returned HTTP {err.code}: "
                           f"{err.read()[:200].decode('utf-8', 'replace')}") from None
    if body.strip() != "ok":
        raise RuntimeError(f"Slack webhook replied: {body[:200]}")
