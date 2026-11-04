#!/usr/bin/env python3
"""Find and score active, individually-maintained Windows-container repos on GitHub."""
import json
import math
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

NOW = datetime.now(timezone.utc)
WINDOW_DAYS = 30
SINCE = NOW - timedelta(days=WINDOW_DAYS)
SINCE_STR = SINCE.strftime("%Y-%m-%d")

QUERIES = [
    "nanoserver",
    "servercore",
    "windowsservercore",
    "windows container",
    "windows containers",
    "windows docker image",
    "windows dockerfile",
    "nano server docker",
    "windows server core docker",
    "hyper-v isolation container",
    "containerd windows",
    "windows container image",
    "windows-container in:topics",
    "windows-containers in:topics",
    "windows-docker in:topics",
    "nanoserver in:topics",
    "windowsservercore in:topics",
    "host compute service",
    "hcs windows",
    "wslc",
    "wsl containers",
    "windows container runtime",
    "process isolation windows",
    "ltsc2025 docker",
    "ltsc2022 docker",
]

EXCLUDED_OWNERS = re.compile(
    r"^(microsoft.*|azure.*|msft.*|kubernetes.*|k8s.*|docker.*|moby|mirantis|"
    r"containerd|opencontainers|rancher.*|cncf|github|actions|dotnet|powershell|"
    r"windows-.*|aws.*|google.*|hashicorp|chocolatey)$",
    re.I,
)
EXCLUDED_NAME_TERMS = re.compile(r"\b(microsoft|azure|kubernetes|docker-library)\b", re.I)

REFERENCE_REPO = "adaskothebeast/nanoserver"

# Tier A: native Windows containers (WCOW images, HCS, Hyper-V/process isolation).
TIER_A = re.compile(
    r"nano ?server|servercore|windows ?server ?core|windows[ -]containers?|windows docker images?|"
    r"windows container base|host compute|\bhcs\b|hyper-v(-| )?isolat|process-isolat|\bwcow\b|"
    r"native windows container|windows-native container",
    re.I,
)
# Tier B: container runtimes/managers that improve running containers on a Windows host.
TIER_B = re.compile(
    r"wsl ?containers?|\bwslc\b|container (runtime|manager|engine)[^.]*windows|"
    r"docker (distribution |engine )?(on|for) windows|containers? for windows|"
    r"container images? natively on windows|windows-native counterpart to a dev container",
    re.I,
)
OFF_TOPIC = re.compile(r"minecraft|fabric mod|folder container|hedera|makepad|openclaw", re.I)
RELEVANCE_MULT = {"A": 1.0, "B": 0.85}

HUMAN_EVENT_TYPES = {
    "IssuesEvent": "issue",
    "IssueCommentEvent": "comment",
    "PullRequestEvent": "pr",
    "PullRequestReviewEvent": "pr_review",
    "PullRequestReviewCommentEvent": "comment",
    "ForkEvent": "fork",
    "WatchEvent": "star",
    "CommitCommentEvent": "comment",
    "DiscussionEvent": "comment",
}


def gh_api(path):
    cmd = ["gh", "api", "-H", "Accept: application/vnd.github+json", path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def is_bot(user):
    if not user:
        return True
    login = (user.get("login") or "").lower()
    return (
        user.get("type") == "Bot"
        or login.endswith("[bot]")
        or login.endswith("-bot")
        or login in {"dependabot", "renovate", "github-actions", "copilot", "imgbot", "allcontributors"}
    )


def search_candidates():
    seen = {}
    for q in QUERIES:
        cmd = [
            "gh", "search", "repos", *q.split(), f"--updated=>={SINCE_STR}",
            "--limit", "100", "--json",
            "fullName,owner,description,stargazersCount,forksCount,updatedAt,isFork,isArchived,url",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"search failed for {q!r}: {r.stderr.strip()}", file=sys.stderr)
            continue
        for repo in json.loads(r.stdout):
            seen.setdefault(repo["fullName"], repo)
    return list(seen.values())


def relevance_tier(repo):
    owner = repo["owner"]
    if repo["fullName"].lower() == REFERENCE_REPO:
        return None
    if repo.get("isArchived") or repo.get("isFork"):
        return None
    if owner.get("type") != "User" or owner.get("is_bot"):
        return None
    if EXCLUDED_OWNERS.match(owner["login"]) or EXCLUDED_NAME_TERMS.search(repo["fullName"]):
        return None
    text = f"{repo['fullName']} {repo.get('description') or ''}"
    if OFF_TOPIC.search(text):
        return None
    if TIER_A.search(text):
        return "A"
    if TIER_B.search(text):
        return "B"
    return None


def human_activity(full):
    events = gh_api(f"/repos/{full}/events?per_page=100") or []
    acts = []
    for e in events:
        t = HUMAN_EVENT_TYPES.get(e["type"])
        ts = parse_ts(e["created_at"])
        if t and ts >= SINCE and not is_bot(e.get("actor")):
            acts.append((t, ts, e["actor"]["login"]))
    return acts


def latest_human_comment(full):
    comments = gh_api(f"/repos/{full}/issues/comments?sort=created&direction=desc&per_page=30&since={SINCE.isoformat()}") or []
    comments = [c for c in comments if not is_bot(c.get("user")) and parse_ts(c["created_at"]) >= SINCE]
    # Issue/PR bodies opened in the window also count as "comments" for semantics.
    issues = gh_api(f"/repos/{full}/issues?state=all&sort=created&direction=desc&per_page=30&since={SINCE.isoformat()}") or []
    for i in issues:
        if not is_bot(i.get("user")) and parse_ts(i["created_at"]) >= SINCE:
            comments.append({"created_at": i["created_at"], "body": f"{i['title']}\n{i.get('body') or ''}",
                             "user": i["user"], "html_url": i["html_url"]})
    comments.sort(key=lambda c: c["created_at"], reverse=True)
    return comments[0] if comments else None


def pr_stats(full):
    prs = gh_api(f"/repos/{full}/pulls?state=all&sort=updated&direction=desc&per_page=50") or []
    human = [p for p in prs if not is_bot(p.get("user"))]
    last_pr = max((parse_ts(p["created_at"]) for p in human), default=None)
    last_merge = max((parse_ts(p["merged_at"]) for p in human if p.get("merged_at")), default=None)
    return last_pr, last_merge


def clones(full):
    # Traffic API needs push access; returns None for repos we don't own.
    data = gh_api(f"/repos/{full}/traffic/clones")
    return data.get("count") if data else None


# ---------- semantic scoring of latest comment ----------
SEM_RULES = [
    ("feature/enhancement", 1.0, r"\b(feature|enhancement|support for|add support|would be (nice|great)|proposal|implement|request)\b"),
    ("technical contribution", 0.9, r"\b(fix(ed|es)?|patch|pr\b|pull request|commit|refactor|dockerfile|ltsc|nanoserver|servercore|build)\b"),
    ("bug report", 0.7, r"\b(bug|error|fail(s|ed|ing)?|crash|broken|exception|doesn'?t work|not working|issue)\b"),
    ("question", 0.6, r"(\?|\bhow (do|can|to)\b|\bis it possible\b)"),
    ("appreciation", 0.5, r"\b(thanks?|thank you|great|awesome|works|lgtm|love)\b"),
]


def comment_semantics(comment):
    if not comment:
        return "none", 0.0
    body = (comment.get("body") or "").strip()
    if len(body) < 3:
        return "empty", 0.1
    best = ("other", 0.4)
    for label, weight, pat in SEM_RULES:
        if re.search(pat, body, re.I) and weight > best[1]:
            best = (label, weight)
    # Longer, substantive comments get a small boost (max +0.2), capped at 1.0.
    depth = min(len(body) / 1000, 0.2)
    return best[0], min(best[1] + depth, 1.0)


# ---------- scoring ----------
def log_score(v, cap):
    return min(math.log1p(v) / math.log1p(cap), 1.0) if v else 0.0


def recency(ts):
    if not ts:
        return 0.0
    days = (NOW - ts).total_seconds() / 86400
    return math.exp(-days / 15)  # half-life ~10 days


WEIGHTS = {
    "stars": 0.15, "forks": 0.10, "clones": 0.10,
    "last_comment": 0.15, "last_pr": 0.10, "last_merge": 0.10,
    "human_activity": 0.15, "comment_semantics": 0.15,
}


def score(m):
    parts = {
        "stars": log_score(m["stars"], 1000),
        "forks": log_score(m["forks"], 200),
        "clones": log_score(m["clones"], 500) if m["clones"] is not None else None,
        "last_comment": recency(m["last_comment_at"]),
        "last_pr": recency(m["last_pr"]),
        "last_merge": recency(m["last_merge"]),
        "human_activity": log_score(len(m["activity"]), 50),
        "comment_semantics": m["sem_score"],
    }
    # Redistribute weight of unavailable metrics (e.g. clones without push access).
    avail = {k: w for k, w in WEIGHTS.items() if parts[k] is not None}
    total_w = sum(avail.values())
    total = sum(parts[k] * w for k, w in avail.items()) / total_w * RELEVANCE_MULT[m["tier"]]
    return round(total * 100, 2), parts


def fmt(ts):
    return ts.strftime("%Y-%m-%d") if ts else "-"


def main():
    cands = [(r, t) for r in search_candidates() if (t := relevance_tier(r))]
    print(f"{len(cands)} candidates after prefilter", file=sys.stderr)
    results = []
    for r, tier in cands:
        full = r["fullName"]
        acts = human_activity(full)
        if not acts:
            continue
        comment = latest_human_comment(full)
        sem_label, sem_score = comment_semantics(comment)
        last_pr, last_merge = pr_stats(full)
        m = {
            "repo": full, "url": r["url"], "description": r.get("description") or "", "tier": tier,
            "stars": r["stargazersCount"], "forks": r["forksCount"], "clones": clones(full),
            "activity": acts,
            "last_comment_at": parse_ts(comment["created_at"]) if comment else None,
            "last_comment_url": comment.get("html_url") if comment else None,
            "last_comment_excerpt": re.sub(r"\s+", " ", (comment or {}).get("body") or "")[:140],
            "sem_label": sem_label, "sem_score": sem_score,
            "last_pr": last_pr, "last_merge": last_merge,
        }
        m["score"], m["parts"] = score(m)
        results.append(m)
        print(f"  scored {full}: {m['score']}", file=sys.stderr)

    results.sort(key=lambda x: x["score"], reverse=True)
    out = []
    for m in results:
        kinds = {}
        for t, _, _ in m["activity"]:
            kinds[t] = kinds.get(t, 0) + 1
        out.append({
            "repo": m["repo"], "url": m["url"], "description": m["description"], "tier": m["tier"],
            "score": m["score"],
            "stars": m["stars"], "forks": m["forks"], "clones": m["clones"],
            "human_activity_30d": kinds, "last_comment": fmt(m["last_comment_at"]),
            "last_comment_url": m["last_comment_url"], "last_comment_excerpt": m["last_comment_excerpt"],
            "comment_semantics": f"{m['sem_label']} ({m['sem_score']:.2f})",
            "last_pr": fmt(m["last_pr"]), "last_merge": fmt(m["last_merge"]),
            "parts": {k: (round(v, 3) if v is not None else None) for k, v in m["parts"].items()},
        })
    with open("results.json", "w") as f:
        json.dump(out, f, indent=2)
    for i, o in enumerate(out[:10], 1):
        print(f"{i:2}. {o['score']:6.2f} [{o['tier']}] {o['repo']}  ★{o['stars']} ⑂{o['forks']}  "
              f"act={o['human_activity_30d']}  comment={o['last_comment']} [{o['comment_semantics']}]  "
              f"PR={o['last_pr']} merge={o['last_merge']}")


if __name__ == "__main__":
    main()
