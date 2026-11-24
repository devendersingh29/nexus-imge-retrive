cd ~
cat > nexus_image_report.py << 'EOF'
#!/usr/bin/env python3
"""
Nexus docker image report (read-only: does NOT touch MinIO/disk and deletes nothing).

Output: nexus_image_report.csv
Columns: folder | service | environment | image | tag | size | pushed | last_downloaded | group | keep

Run:
    python3 nexus_image_report.py

The password is read from the NEXUS_PASS environment variable; if it is not set,
the script asks for it with a hidden prompt.
Every CONFIG value below can also be overridden with an environment variable.
"""
import csv
import getpass
import os
import re
import sys
import time

import requests

# ===================== CONFIG (edit here) =====================
NEXUS_URL = os.environ.get("NEXUS_URL", "https://repo.walkingtree.tech").rstrip("/")
NEXUS_USER = os.environ.get("NEXUS_USER", "admin")
NEXUS_REPO = os.environ.get("NEXUS_REPO", "docker-minio")

# Keep the latest N images per folder + service for each real environment
KEEP_N = int(os.environ.get("KEEP_N", "3"))
# Keep the latest N images per folder + service across ALL feature branches combined
FEATURE_KEEP_N = int(os.environ.get("FEATURE_KEEP_N", "1"))

# Exact branch names that count as real environments. Every other branch
# falls into the "feature" group. Finalize this after reviewing the first run,
# e.g. add "qa-test,python-qa" if those are genuine QA builds.
REAL_ENVS_STR = os.environ.get("REAL_ENVS", "development,qa,main")

# Tags that are never delete candidates (version tags like 1.0.0 are always protected too)
ALWAYS_KEEP_TAGS_STR = os.environ.get("ALWAYS_KEEP_TAGS", "latest")

OUTPUT_CSV = os.environ.get("OUTPUT_CSV", "nexus_image_report.csv")
PAGE_GAP_SECONDS = 0.2  # small pause between API pages to keep load on Nexus low
# ==============================================================

# Take the password from the environment, otherwise prompt (never hardcode it in this file)
NEXUS_PASS = os.environ.get("NEXUS_PASS") or getpass.getpass(f"Nexus password for {NEXUS_USER}: ")

REAL_ENVS = {e.strip().lower() for e in REAL_ENVS_STR.split(",") if e.strip()}
ALWAYS_KEEP_TAGS = {t.strip() for t in ALWAYS_KEEP_TAGS_STR.split(",") if t.strip()}

session = requests.Session()
session.auth = (NEXUS_USER, NEXUS_PASS)


def human(n):
    """Convert a byte count to a human-readable string (e.g. 1.5 GB)."""
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# Trailing build number (-34) or commit hash (-f5d28dd4) on a tag
BUILD_SUFFIX = re.compile(r"-(\d+|[0-9a-f]{6,40})$")
# Plain version tags such as 1.0.0, v2.3
VERSION_TAG = re.compile(r"v?\d+(\.\d+)*")


def detect_env(tag):
    """Derive the environment from the tag.

    Tags look like <branch>-<buildno> (development-34, feature-advisory-608)
    or <branch>-<commit-hash> (manual-f5d28dd4). The environment is the branch part.
    Plain version tags (1.0.0) are labelled "release".
    """
    tag = tag or ""
    if VERSION_TAG.fullmatch(tag):
        return "release"
    env = BUILD_SUFFIX.sub("", tag)
    return env or tag or "unknown"


def fetch_components():
    """Yield every component in the repo, following Nexus pagination (continuationToken)."""
    token = None
    page = 0
    total = 0
    while True:
        params = {"repository": NEXUS_REPO}
        if token:
            params["continuationToken"] = token
        r = session.get(f"{NEXUS_URL}/service/rest/v1/components", params=params, timeout=120)
        r.raise_for_status()
        data = r.json()
        items = data.get("items", [])
        page += 1
        total += len(items)
        # Progress goes to stderr so the terminal does not look stuck
        print(f"  page {page}: +{len(items)} (total {total})", file=sys.stderr, flush=True)
        for item in items:
            yield item
        token = data.get("continuationToken")
        if not token:
            break
        time.sleep(PAGE_GAP_SECONDS)


def main():
    print(f"Repo: {NEXUS_REPO} @ {NEXUS_URL} | KEEP_N={KEEP_N} FEATURE_KEEP_N={FEATURE_KEEP_N} "
          f"REAL_ENVS={sorted(REAL_ENVS)}", file=sys.stderr)

    # 1) Collect one row per image (component)
    rows = []
    for c in fetch_components():
        name = c.get("name", "")      # e.g. coldstar-wms/master-data-service
        tag = c.get("version", "")
        parts = name.split("/")
        folder = parts[0] if len(parts) > 1 else "-"
        service = "/".join(parts[1:]) if len(parts) > 1 else parts[0]

        # Size = sum of all assets (manifest + layers); push date = newest asset timestamp
        assets = c.get("assets", [])
        size = sum(a.get("fileSize") or 0 for a in assets)
        pushed = max((a.get("blobCreated") or a.get("lastModified") or "" for a in assets), default="")
        dl = max((a.get("lastDownloaded") or "" for a in assets), default="")

        rows.append({
            "folder": folder,
            "service": service,
            "environment": detect_env(tag),
            "image": name,
            "tag": tag,
            "size_bytes": size,
            "size": human(size),
            "pushed": pushed,
            "last_downloaded": dl or "never",
        })

    # 2) Retention rules (processed newest first):
    #  - real env branches (development, qa, main...): keep latest KEEP_N per folder + service + env
    #  - all other branches = "feature": keep latest FEATURE_KEEP_N per folder + service (combined)
    #  - 'latest' and version tags (1.0.0) are always kept
    rows.sort(key=lambda r: r["pushed"], reverse=True)
    seen = {}
    for r in rows:
        env = r["environment"]
        is_real = env.lower() in REAL_ENVS
        r["group"] = env if is_real else "feature"
        limit = KEEP_N if is_real else FEATURE_KEEP_N

        if r["tag"] in ALWAYS_KEEP_TAGS or env == "release":
            r["keep"] = "KEEP (protected tag)"
            continue

        key = (r["folder"], r["service"], r["group"])
        seen[key] = seen.get(key, 0) + 1
        r["keep"] = "KEEP" if seen[key] <= limit else "DELETE-CANDIDATE"

    # 3) CSV order: by folder / service / environment, newest push first within each
    rows.sort(key=lambda r: r["pushed"], reverse=True)
    rows.sort(key=lambda r: (r["folder"], r["service"], r["environment"]))

    cols = ["folder", "service", "environment", "image", "tag", "size", "pushed",
            "last_downloaded", "group", "keep"]
    with open(OUTPUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    # 4) Summary
    total = sum(r["size_bytes"] for r in rows)
    cand_rows = [r for r in rows if r["keep"] == "DELETE-CANDIDATE"]
    cand = sum(r["size_bytes"] for r in cand_rows)
    print(f"\nImages: {len(rows)} | listed size: {human(total)} | "
          f"delete candidates: {len(cand_rows)} ({human(cand)}, approx, layers are shared)")

    counts = {}
    for r in rows:
        counts[r["group"]] = counts.get(r["group"], 0) + 1
    print("\nGroups (real envs + 'feature'):")
    for g, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {g:40s} {n}")

    # Branches that fell into "feature": verify none of them is actually a real environment
    branches = {}
    for r in rows:
        if r["group"] == "feature":
            branches[r["environment"]] = branches.get(r["environment"], 0) + 1
    print("\nBranches treated as 'feature' (check none of these is a real env):")
    for b, n in sorted(branches.items(), key=lambda x: -x[1])[:30]:
        print(f"  {b:40s} {n}")

    # Sanity warnings
    if not any(r["size_bytes"] for r in rows):
        print("WARNING: all sizes are 0, Nexus did not return fileSize", file=sys.stderr)
    if not any(r["pushed"] for r in rows):
        print("WARNING: 'pushed' is empty, keep/delete order is unreliable", file=sys.stderr)
    print(f"\nSaved: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
EOF
