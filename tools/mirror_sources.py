#!/usr/bin/env python3
"""Download pinned source archives and upload them as GitHub Release assets.

This script creates or updates the "native-sources" release on the jamly
repository with the pinned source archives. CI then downloads from this
release (via the mirrors array in native-sources.json) instead of the
upstream hosts, removing third-party availability from the critical path.

Usage:
    GITHUB_TOKEN=ghp_... python tools/mirror_sources.py

The token needs the "repo" scope (or write:packages for org repos).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

from native_audio import ROOT, SOURCES, download_source

RELEASE_TAG = "native-sources-v1"
OWNER = "kurtisnotcurtis"
REPO = "jamly"
API_BASE = "https://api.github.com"


def get_token() -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Error: Set GITHUB_TOKEN environment variable", file=sys.stderr)
        sys.exit(1)
    return token


def api_request(method: str, path: str, token: str, data=None) -> dict:
    url = API_BASE + path
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
    }
    body = json.dumps(data).encode() if data else None
    req = Request(url, data=body, headers=headers, method=method)
    print("API request: %s %s" % (method, path), file=sys.stderr)
    try:
        with urlopen(req) as response:
            content = response.read().decode()
            return json.loads(content) if content else {}
    except URLError as err:
        print("API error: %s %s -> %s" % (method, path, err), file=sys.stderr)
        raise


def upload_asset(token: str, upload_url_template: str, file_path: Path) -> None:
    filename = file_path.name
    # GitHub upload URL template looks like: .../assets{?name,label}
    # Replace with actual query params
    upload_url = upload_url_template.replace("{?name,label}", "?name=" + filename)
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/octet-stream",
    }
    with file_path.open("rb") as f:
        data = f.read()
    req = Request(upload_url, data=data, headers=headers, method="POST")
    print("  Uploading: %s" % filename)
    try:
        with urlopen(req) as response:
            response.read()
    except URLError as err:
        print("Upload error for %s: %s" % (filename, err), file=sys.stderr)
        sys.exit(1)
    print("  Uploaded: %s" % filename)


def main() -> None:
    token = get_token()
    print("Token acquired, starting mirror process...")
    cache = ROOT / "build" / "mirror-cache"
    cache.mkdir(parents=True, exist_ok=True)

    # Download all sources
    print("Downloading pinned sources...")
    archives = []
    for name in SOURCES:
        print("  Downloading %s (%s)..." % (name, SOURCES[name]["version"]))
        archive = download_source(name, cache)
        archives.append((name, archive))

    # Create or update the release
    print("\nUpdating GitHub release '%s'..." % RELEASE_TAG)
    release = None
    try:
        release = api_request("GET", "/repos/%s/%s/releases/tags/%s" % (OWNER, REPO, RELEASE_TAG), token)
        print("  Release exists (id=%d)" % release["id"])
        if release.get("immutable"):
            print("  Release is immutable, deleting release and tag...")
            api_request("DELETE", "/repos/%s/%s/releases/%d" % (OWNER, REPO, release["id"]), token)
            api_request("DELETE", "/repos/%s/%s/git/refs/tags/%s" % (OWNER, REPO, RELEASE_TAG), token)
            release = None
    except URLError:
        release = None
    
    if release is None:
        print("  Creating release (draft)...")
        release = api_request("POST", "/repos/%s/%s/releases" % (OWNER, REPO), token, {
            "tag_name": RELEASE_TAG,
            "name": "Pinned native source archives",
            "body": "Mirror of pinned source archives for CI builds. Updated automatically when source pins change.",
            "draft": True,
            "prerelease": False,
        })

    # Upload each archive
    print("\nUploading source archives...")
    for name, archive in archives:
        upload_asset(token, release["upload_url"], archive)

    # Publish the release
    print("\nPublishing release...")
    api_request("PATCH", "/repos/%s/%s/releases/%d" % (OWNER, REPO, release["id"]), token, {"draft": False})

    print("\nDone. CI will now download from the GitHub release instead of upstream hosts.")


if __name__ == "__main__":
    main()
