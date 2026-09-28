"""Turn a bug report into a fix brief: a GitHub issue, a file, or plain text.

Issue text and comments are written by third parties. They are carried into
prompts as quoted, untrusted data, never as instructions to the runner.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
BODY_LIMIT = 12000
COMMENTS_LIMIT = 10000
_URL = re.compile(r"^https?://github\.com/([\w.-]+)/([\w.-]+)/(?:issues|pull)/(\d+)(?:[/?#].*)?$")
_SHORT = re.compile(r"^([\w.-]+)/([\w.-]+)#(\d+)$")
_LOCAL = re.compile(r"^#?(\d+)$")
_REMOTE = re.compile(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")


def origin_repository(workspace) -> tuple[str, str] | None:
    result = subprocess.run(["git", "-C", str(workspace), "remote", "get-url", "origin"],
                            capture_output=True, text=True)
    match = _REMOTE.search(result.stdout.strip()) if result.returncode == 0 else None
    return (match.group(1), match.group(2)) if match else None


def parse_reference(text, workspace=None) -> dict | None:
    """Recognize a GitHub URL, ``owner/repo#N``, or ``#N`` (resolved via ``origin``)."""
    text = (text or "").strip()
    for pattern in (_URL, _SHORT):
        match = pattern.match(text)
        if match:
            owner, repo, number = match.groups()
            return {"owner": owner, "repo": repo.removesuffix(".git"), "number": int(number)}
    match = _LOCAL.match(text)
    if match and workspace is not None:
        origin = origin_repository(workspace)
        if not origin:
            raise ValueError(f"{text!r} needs a GitHub 'origin' remote in {workspace}; use owner/repo#N")
        return {"owner": origin[0], "repo": origin[1], "number": int(match.group(1))}
    return None


def _get(url, token=None, opener=None):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "autocode-fix",
               "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with (opener or urllib.request.urlopen)(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        hint = " (set GITHUB_TOKEN for private repositories or rate limits)" if error.code in (401, 403, 404) else ""
        raise RuntimeError(f"GitHub API {error.code} for {url}{hint}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Cannot reach GitHub API at {url}: {error.reason}") from error


def fetch(reference, *, token=None, api=None, opener=None) -> dict:
    """Fetch an issue and its comments through the GitHub REST API."""
    api = (api or os.environ.get("AUTOCODE_GITHUB_API") or API).rstrip("/")
    token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    base = f"{api}/repos/{reference['owner']}/{reference['repo']}/issues/{reference['number']}"
    issue = _get(base, token, opener)
    comments = _get(base + "/comments?per_page=100", token, opener) if issue.get("comments") else []
    return {
        "source": "github",
        "url": issue.get("html_url") or f"https://github.com/{reference['owner']}/{reference['repo']}/issues/{reference['number']}",
        "repository": f"{reference['owner']}/{reference['repo']}",
        "number": reference["number"],
        "title": issue.get("title") or "",
        "body": issue.get("body") or "",
        "state": issue.get("state"),
        "is_pull_request": "pull_request" in issue,
        "labels": [label.get("name", "") if isinstance(label, dict) else str(label)
                   for label in issue.get("labels") or []],
        "author": (issue.get("user") or {}).get("login"),
        "comments": [{"author": (c.get("user") or {}).get("login"), "body": c.get("body") or "",
                      "created_at": c.get("created_at")} for c in comments if isinstance(c, dict)],
    }


def from_text(text, *, source="text") -> dict:
    lines = text.strip().splitlines() or [""]
    title = lines[0].lstrip("# ").strip()[:200]
    return {"source": source, "url": None, "repository": None, "number": None, "title": title,
            "body": text.strip(), "state": None, "is_pull_request": False, "labels": [], "author": None,
            "comments": []}


def from_file(path) -> dict:
    path = Path(path)
    text = path.read_text()
    if path.suffix == ".json":
        data = json.loads(text)
        if not isinstance(data, dict) or not str(data.get("title") or data.get("body") or "").strip():
            raise ValueError(f"{path} must be a JSON object with a title or body")
        issue = from_text(f"{data.get('title', '')}\n\n{data.get('body', '')}", source=f"file:{path.name}")
        issue.update({k: data[k] for k in ("title", "url", "labels", "comments", "number", "repository") if k in data})
        return issue
    return from_text(text, source=f"file:{path.name}")


def load(issue=None, *, issue_file=None, workspace=None, opener=None) -> dict:
    if issue_file:
        return from_file(issue_file)
    if not issue or not issue.strip():
        raise ValueError("Give an issue: a GitHub URL, owner/repo#N, #N, --issue-file, or a text description")
    reference = parse_reference(issue, workspace)
    return fetch(reference, opener=opener) if reference else from_text(issue)


def _clean(text, limit):
    text = re.sub(r"<!--.*?-->", "", text or "", flags=re.S).strip()
    return text if len(text) <= limit else text[:limit] + f"\n[... truncated {len(text) - limit} characters]"


def render(issue) -> str:
    """The issue as quoted, bounded Markdown for a prompt."""
    lines = [f"Title: {issue['title']}"]
    if issue.get("url"):
        lines.append(f"URL: {issue['url']}")
    if issue.get("labels"):
        lines.append("Labels: " + ", ".join(issue["labels"]))
    lines += ["", "Report:", _clean(issue.get("body", ""), BODY_LIMIT) or "(empty)"]
    budget = COMMENTS_LIMIT
    comments = [c for c in issue.get("comments") or [] if (c.get("body") or "").strip()]
    if comments:
        lines += ["", "Discussion (oldest first):"]
    for comment in comments:
        if budget <= 0:
            lines.append(f"[... {len(comments)} comments total; the rest were omitted]")
            break
        body = _clean(comment["body"], budget)
        budget -= len(body)
        lines.append(f"--- comment by {comment.get('author') or 'unknown'}:\n{body}")
    return "\n".join(lines)


def slug(issue) -> str:
    prefix = f"{issue['number']}-" if issue.get("number") else ""
    words = re.sub(r"[^a-z0-9]+", "-", (issue.get("title") or "fix").lower()).strip("-")
    return (prefix + words)[:48].strip("-") or "fix"
