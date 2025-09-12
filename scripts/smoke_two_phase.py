"""
Tiny smoke test for two-phase material processing.

It will:
- Optionally login (if email/password provided) or use a provided ACCESS TOKEN
- Upload a PDF/PNG/JPG
- Poll until overview is ready (status -> idle and light_overview present)
- Trigger detailed notes generation
- Poll until detailed notes are ready (status -> completed and processed_content present)

Requirements:
- Backend server running (default http://localhost:8000)
- A valid access token OR credentials to login

Usage (PowerShell):
  python scripts/smoke_two_phase.py --file path\\to\\sample.pdf --email you@example.com --password yourpass
  # or with a token
  $env:ACCESS_TOKEN = "<your JWT>"; python scripts/smoke_two_phase.py --file path\\to\\sample.png
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional

import requests


def _login(base_url: str, email: str, password: str) -> str:
    url = f"{base_url}/auth/login"
    r = requests.post(url, json={"email": email, "password": password}, timeout=30)
    r.raise_for_status()
    data = r.json().get("data") or {}
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"Login succeeded but no access_token found: {r.text}")
    return token


def _upload(base_url: str, token: str, file_path: str, title: str) -> dict:
    url = f"{base_url}/materials/upload"
    headers = {"Authorization": f"Bearer {token}"}
    with open(file_path, "rb") as f:
        files = {"file": (os.path.basename(file_path), f)}
        data = {"title": title}
        r = requests.post(url, headers=headers, files=files, data=data, timeout=60)
    r.raise_for_status()
    return r.json().get("data") or {}


def _get_material(base_url: str, token: str, material_id: str) -> dict:
    url = f"{base_url}/materials/{material_id}"
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json().get("data") or {}


def _trigger_notes(base_url: str, token: str, material_id: str) -> None:
    url = f"{base_url}/materials/{material_id}/generate-notes"
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.post(url, headers=headers, timeout=30)
    r.raise_for_status()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Two-phase processing smoke test")
    parser.add_argument("--base-url", default="http://localhost:8000/api/v1", help="API base URL (default: %(default)s)")
    parser.add_argument("--email", help="Email for login (optional if ACCESS_TOKEN provided)")
    parser.add_argument("--password", help="Password for login (optional if ACCESS_TOKEN provided)")
    parser.add_argument("--token", help="Access token (or set ACCESS_TOKEN env)")
    parser.add_argument("--file", required=True, help="Path to a small PDF/PNG/JPG to upload")
    parser.add_argument("--title", default="Smoke Test Upload", help="Title for the material")
    parser.add_argument("--overview-timeout", type=int, default=120, help="Seconds to wait for overview")
    parser.add_argument("--notes-timeout", type=int, default=300, help="Seconds to wait for detailed notes")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="Polling interval in seconds")

    args = parser.parse_args(argv)

    token = args.token or os.getenv("ACCESS_TOKEN")
    if not token:
        if not (args.email and args.password):
            print("ERROR: Provide --token or set ACCESS_TOKEN, or provide --email and --password", file=sys.stderr)
            return 2
        print("Logging in to obtain access token…")
        token = _login(args.base_url, args.email, args.password)
        print("Login OK")

    if not os.path.isfile(args.file):
        print(f"ERROR: File not found: {args.file}", file=sys.stderr)
        return 2

    print("Uploading material…")
    up = _upload(args.base_url, token, args.file, args.title)
    material_id = up.get("material_id")
    if not material_id:
        print(f"ERROR: No material_id in upload response: {up}", file=sys.stderr)
        return 1
    print(f"Upload OK: material_id={material_id}")

    # Wait for overview
    print("Polling for overview…")
    deadline = time.time() + args.overview_timeout
    overview_len = None
    while time.time() < deadline:
        mat = _get_material(args.base_url, token, material_id)
        status = mat.get("status")
        overview = mat.get("light_overview")
        if overview:
            overview_len = len(overview)
        if status == "idle" and overview:
            print(f"Overview ready (len={overview_len})")
            break
        time.sleep(args.poll_interval)
    else:
        print("ERROR: Overview not ready before timeout", file=sys.stderr)
        return 1

    # Trigger detailed notes
    print("Triggering detailed notes generation…")
    _trigger_notes(args.base_url, token, material_id)

    # Wait for detailed notes
    print("Polling for detailed notes…")
    deadline = time.time() + args.notes_timeout
    detail_len = None
    while time.time() < deadline:
        mat = _get_material(args.base_url, token, material_id)
        status = mat.get("status")
        detailed = mat.get("processed_content")
        if detailed:
            detail_len = len(detailed)
        if status == "completed" and detailed:
            print(f"Detailed notes ready (len={detail_len})")
            print("Smoke test PASSED ✔")
            return 0
        if status == "failed":
            print("ERROR: Detailed notes generation failed", file=sys.stderr)
            return 1
        time.sleep(args.poll_interval)

    print("ERROR: Detailed notes not ready before timeout", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
