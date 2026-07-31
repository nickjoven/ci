#!/usr/bin/env python3
"""ci-audit — make CI coverage a convergent property instead of a task.

Enumerates the account's repositories, detects what each one is written in,
and opens a pull request adding the shared-CI stub anywhere it is missing.

The premise: scaffolding at creation time fails, because you can forget to
use the scaffold. An audit that reconciles cannot be forgotten. Coverage
stops being something you do and becomes something that is true.

Same shape as harmonics/substrate-maintenance.yml — detect drift, reconcile,
open the change for review. Nothing here merges on its own; judgment stays
with the human.

    python3 scripts/audit.py                 # dry run — report only
    python3 scripts/audit.py --apply         # open PRs
    python3 scripts/audit.py --only ket      # restrict to named repos

Requires: gh, authenticated. In CI, needs a PAT with `repo` scope — the
default GITHUB_TOKEN cannot write to other repositories.
"""

import argparse
import base64
import json
import subprocess
import sys
from datetime import datetime, timezone

OWNER = "nickjoven"
CI_REPO = "nickjoven/ci"
WORKFLOW_PATH = ".github/workflows/ci.yml"
BRANCH = "ci/adopt-shared-ci"

# Repos that should not be audited: vendored, archival, or deliberately bare.
SKIP = {
    "ci",              # this repo defines the workflows; it is not a caller
    "dsa-prep", "lecture-notes", "steps-to-starting-a-project",
    "learning-websockets", "test", "t7",
}

MARKERS = {
    "rust": ["Cargo.toml"],
    "python": ["pyproject.toml", "setup.py", "requirements.txt"],
}


def gh(*args, check=True):
    """Run gh and return stdout, or None on failure."""
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        if check:
            return None
        return None
    return r.stdout


def gh_json(*args):
    out = gh(*args)
    if out is None:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def list_repos():
    data = gh_json(
        "repo", "list", OWNER, "--limit", "300", "--source",
        "--json", "name,isArchived,isFork,defaultBranchRef,primaryLanguage,pushedAt",
    )
    if data is None:
        sys.exit("could not list repositories — is gh authenticated?")
    out = []
    for r in data:
        if r["isFork"] or r["isArchived"] or r["name"] in SKIP:
            continue
        if not r.get("defaultBranchRef"):
            continue  # empty repo, nothing to run against
        out.append(r)
    return sorted(out, key=lambda r: r["name"])


def root_files(repo):
    data = gh_json("api", f"repos/{OWNER}/{repo}/contents")
    if not isinstance(data, list):
        return set()
    return {x["name"] for x in data}


def detect_kind(files):
    for kind, markers in MARKERS.items():
        if any(m in files for m in markers):
            return kind
    return None


def workflow_state(repo):
    """Return (state, names).

    adopted — a workflow references the shared CI repo
    own     — the repo has workflows of its own; leave it alone
    none    — no workflows at all; this is the only case we act on

    Checking for a file literally named ci.yml is not good enough: harmonics
    carries thirteen workflows and names its gate ci-gates.yml. Treating that
    as uncovered would open a noise PR on the most rigorously tested repo in
    the account — and noise is the thing this system exists to prevent.
    """
    data = gh_json("api", f"repos/{OWNER}/{repo}/contents/.github/workflows")
    if not isinstance(data, list) or not data:
        return "none", []
    names = sorted(x["name"] for x in data)
    stub = gh_json("api", f"repos/{OWNER}/{repo}/contents/{WORKFLOW_PATH}")
    if isinstance(stub, dict) and stub.get("content"):
        try:
            body = base64.b64decode(stub["content"]).decode("utf-8", "replace")
            if CI_REPO in body:
                return "adopted", names
        except Exception:
            pass
    return "own", names


def stale_days(repo):
    pushed = repo.get("pushedAt")
    if not pushed:
        return None
    try:
        when = datetime.strptime(pushed, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - when).days


def open_pr_exists(repo):
    data = gh_json("api", f"repos/{OWNER}/{repo}/pulls?state=open&head={OWNER}:{BRANCH}")
    return isinstance(data, list) and len(data) > 0


def stub_for(kind):
    with open(f"{sys.path[0]}/../stubs/{kind}.yml", encoding="utf-8") as fh:
        return fh.read()


def adopt(repo, default_branch, kind):
    """Create branch, add stub, open PR. Returns the PR url or an error string."""
    ref = gh_json("api", f"repos/{OWNER}/{repo}/git/ref/heads/{default_branch}")
    if not ref:
        return "could not read default branch ref"
    sha = ref["object"]["sha"]

    # Branch may already exist from a previous run that failed midway.
    gh("api", f"repos/{OWNER}/{repo}/git/refs", "-X", "POST",
       "-f", f"ref=refs/heads/{BRANCH}", "-f", f"sha={sha}", check=False)

    content = base64.b64encode(stub_for(kind).encode()).decode()
    put = gh_json(
        "api", f"repos/{OWNER}/{repo}/contents/{WORKFLOW_PATH}", "-X", "PUT",
        "-f", "message=ci: adopt shared workflow from nickjoven/ci",
        "-f", f"content={content}", "-f", f"branch={BRANCH}",
    )
    if put is None:
        return "could not write workflow file"

    body = (
        f"Adopts the shared `{kind}` workflow from [{CI_REPO}]"
        f"(https://github.com/{CI_REPO}).\n\n"
        "This file is a reference, not an implementation — the eight lines here call a "
        "reusable workflow maintained in one place. Bumping an action version there "
        "propagates everywhere, so no repository accumulates CI of its own to maintain.\n\n"
        "Opened automatically by `ci-audit`. Defaults are tolerant on purpose: a rollout "
        "that turns several repos red at once trains you to ignore the notification.\n"
    )
    pr = gh_json(
        "api", f"repos/{OWNER}/{repo}/pulls", "-X", "POST",
        "-f", "title=ci: adopt shared workflow",
        "-f", f"head={BRANCH}", "-f", f"base={default_branch}", "-f", f"body={body}",
    )
    if pr is None:
        return "could not open pull request"
    return pr.get("html_url", "opened")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="open PRs (default is dry run)")
    ap.add_argument("--only", nargs="*", help="restrict to these repo names")
    ap.add_argument("--max-age", type=int, default=180, metavar="DAYS",
                    help="do not open PRs against repos dormant longer than this (default 180)")
    args = ap.parse_args()

    repos = list_repos()
    if args.only:
        wanted = set(args.only)
        repos = [r for r in repos if r["name"] in wanted]

    adopted, own, actionable, dormant, na = [], [], [], [], []

    print(f"auditing {len(repos)} repositories\n")
    for r in repos:
        name = r["name"]
        state, names = workflow_state(name)

        if state == "adopted":
            adopted.append(name)
            print(f"  adopted  {name}")
            continue
        if state == "own":
            own.append(name)
            print(f"  own      {name}  ({len(names)} workflow{'s' if len(names) != 1 else ''}: {', '.join(names[:3])}{'…' if len(names) > 3 else ''})")
            continue

        kind = detect_kind(root_files(name))
        if kind is None:
            na.append(name)
            continue

        age = stale_days(r)
        if age is not None and age > args.max_age and not args.only:
            dormant.append((name, age))
            print(f"  dormant  {name}  ({kind}, {age}d since push — not touching)")
            continue

        if open_pr_exists(name):
            print(f"  pending  {name}  (PR already open)")
            continue

        actionable.append((name, kind))
        if not args.apply:
            print(f"  MISSING  {name}  ({kind}, {age}d since push)")
            continue

        result = adopt(name, r["defaultBranchRef"]["name"], kind)
        print(f"  opened   {name}  ({kind})  {result}")

    print(
        f"\n{len(adopted)} adopted · {len(own)} own CI · {len(actionable)} actionable · "
        f"{len(dormant)} dormant · {len(na)} not applicable"
    )
    if actionable and not args.apply:
        names = ", ".join(n for n, _ in actionable)
        print(f"\nactionable: {names}")
        print("re-run with --apply to open pull requests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
