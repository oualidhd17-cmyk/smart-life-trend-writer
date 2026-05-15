from __future__ import annotations

import csv
import pickle
import time
from pathlib import Path
import os

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

ROOT = Path(__file__).resolve().parent
CSV_FILE = ROOT / "output" / "blogger_ready_posts.csv"
CLIENT_SECRET_FILE = ROOT / "client_secret.json"
TOKEN_FILE = ROOT / "token_blogger.pickle"
UPLOADED_LOG = ROOT / "uploaded_titles.txt"

BLOG_ID = os.getenv("BLOG_ID", "4369063726049217258")

SCOPES = ["https://www.googleapis.com/auth/blogger"]

MAX_UPLOADS_PER_RUN = 5
DELAY_BETWEEN_UPLOADS_SECONDS = 180


def get_service():
    creds = None

    if TOKEN_FILE.exists():
        with TOKEN_FILE.open("rb") as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CLIENT_SECRET_FILE),
                SCOPES,
            )
            creds = flow.run_local_server(port=0)

        with TOKEN_FILE.open("wb") as token:
            pickle.dump(creds, token)

    return build("blogger", "v3", credentials=creds)


def normalize_title(title: str) -> str:
    return " ".join(title.lower().strip().split())


def load_uploaded_titles() -> set[str]:
    if not UPLOADED_LOG.exists():
        return set()

    return {
        normalize_title(line)
        for line in UPLOADED_LOG.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def save_uploaded_title(title: str) -> None:
    with UPLOADED_LOG.open("a", encoding="utf-8") as f:
        f.write(title.strip() + "\n")


def read_html_file(path_text: str) -> str:
    path = Path(path_text)

    if not path.exists():
        raise FileNotFoundError(f"HTML file not found: {path}")

    return path.read_text(encoding="utf-8")


def upload_draft(service, row: dict) -> dict:
    title = row["title"].strip()
    content = read_html_file(row["html_file"].strip())
    labels = [x.strip() for x in row.get("labels", "").split(",") if x.strip()]

    body = {
        "kind": "blogger#post",
        "title": title,
        "content": content,
        "labels": labels,
    }

    request = service.posts().insert(
        blogId=BLOG_ID,
        body=body,
        isDraft=True,
        fetchImages=False,
    )

    return request.execute()


def should_skip_title(title: str) -> bool:
    bad_phrases = [
        "reportedly preparing legal action",
        "cuts nearly",
        "who decides what ai tells you",
        "cat wu says",
        "campbell brown",
    ]

    lower = title.lower()

    return any(p in lower for p in bad_phrases)


def main():
    if not CSV_FILE.exists():
        raise FileNotFoundError(f"Missing CSV file: {CSV_FILE}")

    service = get_service()
    uploaded_titles = load_uploaded_titles()

    uploaded = 0
    skipped = 0

    seen_this_run = set()

    with CSV_FILE.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        for row in reader:
            title = row.get("title", "").strip()

            if not title:
                continue

            title_key = normalize_title(title)

            if title_key in uploaded_titles:
                print(f"Skipping already uploaded: {title}")
                skipped += 1
                continue

            if title_key in seen_this_run:
                print(f"Skipping duplicate in CSV: {title}")
                skipped += 1
                continue

            if should_skip_title(title):
                print(f"Skipping weak/news-style topic: {title}")
                skipped += 1
                continue

            seen_this_run.add(title_key)

            if uploaded >= MAX_UPLOADS_PER_RUN:
                print(f"\nReached limit for this run: {MAX_UPLOADS_PER_RUN}")
                break

            try:
                result = upload_draft(service, row)
                uploaded += 1

                save_uploaded_title(title)
                uploaded_titles.add(title_key)

                print(f"Uploaded draft: {result.get('title')}")
                print(f"URL: {result.get('url', 'Draft created')}")

                if uploaded < MAX_UPLOADS_PER_RUN:
                    print(f"Waiting {DELAY_BETWEEN_UPLOADS_SECONDS} seconds...")
                    time.sleep(DELAY_BETWEEN_UPLOADS_SECONDS)

            except HttpError as exc:
                print(f"Failed uploading: {title}")
                print(exc)

                if "rateLimitExceeded" in str(exc) or "429" in str(exc):
                    print("Rate limit reached. Stop now and try again later.")
                    break

            except Exception as exc:
                print(f"Failed uploading: {title}")
                print(exc)

    print(f"\nDone. Uploaded drafts: {uploaded}. Skipped: {skipped}")


if __name__ == "__main__":
    main()
