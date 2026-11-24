# Nexus Docker Image Report

Read-only report of the Docker images in a Nexus repository. It lists every image
with its size, push date and last download, and marks each one as `KEEP` or
`DELETE-CANDIDATE` based on a retention policy.

**The script never deletes anything.** It only calls the Nexus read API and writes
a CSV. It does not touch MinIO or the disk.

## Quick start

Run these steps in order, in the same terminal session.

### Step 1: Check that Python is available

```bash
python3 --version
```

If it prints a version (3.6 or newer), continue. If you see `command not found`, install it:

```bash
sudo apt update && sudo apt install -y python3 python3-pip
```

Check the `requests` library and install it if it is missing:

```bash
python3 -c "import requests" 2>/dev/null && echo "requests ok" || sudo apt install -y python3-requests
```

### Step 2: Set the password variable

Option A: type the password directly (put a space before `export` so it is not saved in shell history):

```bash
 export NEXUS_PASS='your-nexus-password'
```

Option B: hidden prompt, nothing is shown on screen:

```bash
read -s -p "Nexus password: " NEXUS_PASS; echo
export NEXUS_PASS
```

Check that it is set (prints only `set` or `not set`, never the password):

```bash
[ -n "$NEXUS_PASS" ] && echo "set" || echo "not set"
```

Optional: set the other variables if they differ from the defaults (see the Configuration table):

```bash
export NEXUS_URL=https://repo.walkingtree.tech
export NEXUS_USER=admin
export NEXUS_REPO=docker-minio
export KEEP_N=3
export FEATURE_KEEP_N=1
export REAL_ENVS="development,qa,main"
export ALWAYS_KEEP_TAGS="latest"
```

If `NEXUS_PASS` is not set, the script asks for the password with a hidden prompt.

> Never commit the real password to git. Keep `NEXUS_PASS_DEFAULT` in the script empty.

### Step 3: Run the script

```bash
python3 nexus_image_report.py
```

For a long run that must survive an SSH disconnect (Step 2 must be done first):

```bash
nohup python3 nexus_image_report.py > report.log 2>&1 &
tail -f report.log
```

Output file: `nexus_image_report.csv`

## Configuration

Defaults live in the CONFIG block at the top of `nexus_image_report.py`.
Any value can be overridden with an environment variable.

| Variable | Default | Meaning |
|---|---|---|
| NEXUS_URL | https://repo.walkingtree.tech | Nexus base URL |
| NEXUS_USER | admin | Nexus username |
| NEXUS_PASS | (hidden prompt if unset) | Nexus password |
| NEXUS_REPO | docker-minio | Docker repository name |
| KEEP_N | 3 | Latest N images kept per folder + service for each real environment |
| FEATURE_KEEP_N | 1 | Latest N images kept per folder + service across all feature branches combined |
| REAL_ENVS | development,qa,main | Exact branch names treated as real environments |
| ALWAYS_KEEP_TAGS | latest | Tags that are never delete candidates |
| OUTPUT_CSV | nexus_image_report.csv | Output file name |

Example override for a single run:

```bash
REAL_ENVS="development,qa,qa-test,main" KEEP_N=5 python3 nexus_image_report.py
```

## How it works

Image tags follow the pattern `<branch>-<build-number>` or `<branch>-<commit-hash>`,
for example `development-34`, `feature-advisory-608`, `manual-f5d28dd4`.

1. The environment is the branch part of the tag (the trailing build number or hash is removed).
2. Plain version tags such as `1.0.0` are labelled `release`.
3. A branch listed in `REAL_ENVS` keeps its own group. Every other branch goes into one `feature` group.
4. Images are sorted newest first by push date. Per folder + service + group, the newest N are `KEEP`, the rest are `DELETE-CANDIDATE`.
5. `latest`, `release` (version tags) and anything in `ALWAYS_KEEP_TAGS` are always `KEEP (protected tag)`.

## Output columns

| Column | Meaning |
|---|---|
| folder | First part of the image path (for example `truops`) |
| service | Rest of the image path |
| environment | Branch part of the tag |
| image | Full image name |
| tag | Image tag |
| size | Sum of all asset sizes (manifest + layers) |
| pushed | Newest asset creation time |
| last_downloaded | Last download time, or `never` |
| group | Real environment name, or `feature` |
| keep | `KEEP`, `KEEP (protected tag)` or `DELETE-CANDIDATE` |

## After the run

The script prints a summary: image count, total size, delete candidates, images per
group, and the list of branches treated as `feature`.

1. Check the "Branches treated as 'feature'" list. If a real environment is in it, add it to `REAL_ENVS` and run again.
2. Review the `DELETE-CANDIDATE` rows in the CSV.
3. Ask the owners before removing anything from third-party or other teams' folders (for example `ibm/` or `mitra/`).

## Notes and limits

- Sizes are approximate. Docker layers are shared between images, so the total of delete candidates overstates the space that would actually be freed.
- Run at a quiet time on a large repo. The script pauses 0.2 s between pages to keep the load low.
- Rough run time: about 30-60 s per 1,000 images.

## Troubleshooting

| Problem | Likely cause |
|---|---|
| `python3: command not found` | Python is not installed, see Step 1 |
| HTTP 401 | Wrong username or password, or `NEXUS_PASS` is not set in this terminal |
| Timeout or connection error | Nexus is busy, or the network cannot reach `NEXUS_URL` |
| `ModuleNotFoundError: requests` | Install python3-requests, see Step 1 |
| WARNING: all sizes are 0 | Nexus did not return `fileSize` for the assets |
| WARNING: pushed is empty | No timestamps returned, so the keep/delete order is unreliable |
| Terminal looks stuck | Normal for a large repo, check the `page N` progress lines |
