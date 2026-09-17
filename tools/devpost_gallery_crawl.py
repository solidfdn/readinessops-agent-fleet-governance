#!/usr/bin/env python3
"""Fetch all public All Things Agentic Hackathon submissions without paid APIs.

Outputs under --output-dir:
- gallery_pages.jsonl
- gallery_index.csv
- projects.jsonl
- failures.csv
- crawl_summary.json
- all_things_agentic_raw.zip
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import random
import re
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, Tag

GALLERY = "https://allthingsagentichackathon.devpost.com/project-gallery"
EXPECTED_TOTAL = 1841
PAGE_COUNT = 77
PAGE_SIZE = 24
LAST_PAGE_SIZE = 17
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Safari/537.36"
)

SECTION_ALIASES = {
    "inspiration": ("inspiration",),
    "what_it_does": ("what it does", "what does it do", "overview", "solution"),
    "how_built": ("how we built it", "how i built it", "how it works", "architecture"),
    "challenges": ("challenges", "challenges we ran into", "challenges i ran into"),
    "accomplishments": (
        "accomplishments that we're proud of",
        "accomplishments that we are proud of",
        "accomplishments",
    ),
    "learned": ("what we learned", "what i learned"),
    "next": ("what's next", "whats next", "what is next"),
    "built_with": ("built with",),
    "try_it_out": ("try it out",),
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def norm(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        fh.flush()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def canonical_project_url(href: str, base: str) -> str | None:
    absolute = urljoin(base, href)
    parsed = urlparse(absolute)
    if parsed.netloc.lower() not in {"devpost.com", "www.devpost.com"}:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) != 2 or parts[0] != "software":
        return None
    return urlunparse(("https", "devpost.com", f"/software/{parts[1]}", "", "", ""))


@dataclass
class FetchResult:
    status: int | None
    text: str | None
    error: str | None
    attempts: int
    elapsed: float


class Client:
    def __init__(self, delay: float, timeout: float, attempts: int) -> None:
        self.delay = max(0.0, delay)
        self.timeout = timeout
        self.attempts = attempts
        self.last_request = 0.0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,ja;q=0.6",
            }
        )

    def get(self, url: str) -> FetchResult:
        started = time.monotonic()
        last_status: int | None = None
        last_error: str | None = None
        for attempt in range(1, self.attempts + 1):
            wait = self.delay - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait + random.uniform(0, min(0.2, self.delay / 2 if self.delay else 0)))
            try:
                response = self.session.get(url, timeout=self.timeout, allow_redirects=True)
                self.last_request = time.monotonic()
                last_status = response.status_code
                if response.status_code == 200 and response.text:
                    return FetchResult(last_status, response.text, None, attempt, round(time.monotonic() - started, 3))
                last_error = f"HTTP {response.status_code}"
                if response.status_code not in {408, 425, 429, 500, 502, 503, 504}:
                    break
            except requests.RequestException as exc:
                self.last_request = time.monotonic()
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < self.attempts:
                time.sleep(min(90, 2 ** (attempt - 1) + random.uniform(0.2, 1.0)))
        return FetchResult(last_status, None, last_error or "unknown error", self.attempts, round(time.monotonic() - started, 3))


def card_container(anchor: Tag) -> Tag | None:
    for parent in anchor.parents:
        if not isinstance(parent, Tag):
            continue
        classes = " ".join(parent.get("class", []))
        if parent.name in {"article", "li"} or re.search(r"gallery|project|submission|software", classes, re.I):
            return parent
    return None


def parse_gallery(page: int, url: str, text: str) -> dict[str, Any]:
    soup = BeautifulSoup(text, "lxml")
    seen: set[str] = set()
    projects: list[dict[str, str]] = []
    for anchor in soup.select('a[href*="/software/"]'):
        href = anchor.get("href")
        if not href:
            continue
        project_url = canonical_project_url(href, url)
        if not project_url or project_url in seen:
            continue
        seen.add(project_url)
        container = card_container(anchor)
        title = norm(anchor.get_text(" ", strip=True))
        tagline = ""
        if container is not None:
            heading = container.find(["h1", "h2", "h3", "h4", "h5", "h6"])
            if isinstance(heading, Tag):
                title = norm(heading.get_text(" ", strip=True)) or title
            candidates: list[str] = []
            for node in container.find_all(["p", "div"], limit=16):
                candidate = norm(node.get_text(" ", strip=True))
                if candidate and candidate != title and len(candidate) >= 8:
                    candidates.append(candidate)
            if candidates:
                tagline = min(candidates, key=lambda s: (abs(len(s) - 140), len(s)))
        projects.append({"url": project_url, "title_from_gallery": title, "tagline_from_gallery": tagline})
    expected = LAST_PAGE_SIZE if page == PAGE_COUNT else PAGE_SIZE
    page_text = norm(soup.get_text(" ", strip=True))
    match = re.search(r"(\d[\d,]*)\s*[–-]\s*(\d[\d,]*)\s+of\s+(\d[\d,]*)", page_text)
    return {
        "page": page,
        "url": url,
        "fetched_at": now(),
        "displayed_range": match.group(0) if match else "",
        "expected_count": expected,
        "project_count": len(projects),
        "count_ok": len(projects) == expected,
        "projects": projects,
        "html_sha256": hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest(),
    }


def heading_key(text: str) -> str | None:
    lowered = norm(text).lower().strip("#: ")
    for key, aliases in SECTION_ALIASES.items():
        for alias in aliases:
            if lowered == alias or lowered.startswith(alias + " "):
                return key
    return None


def extract_sections(soup: BeautifulSoup) -> tuple[dict[str, str], dict[str, str]]:
    canonical: dict[str, str] = {}
    all_sections: dict[str, str] = {}
    for heading in soup.find_all(["h2", "h3"]):
        if not isinstance(heading, Tag):
            continue
        label = norm(heading.get_text(" ", strip=True))
        if not label:
            continue
        chunks: list[str] = []
        total = 0
        for sibling in heading.next_siblings:
            if isinstance(sibling, Tag) and sibling.name in {"h2", "h3"}:
                break
            value = norm(sibling.get_text(" ", strip=True) if isinstance(sibling, Tag) else str(sibling))
            if value:
                chunks.append(value)
                total += len(value)
            if total >= 12000:
                break
        value = norm(" ".join(chunks))
        if not value:
            continue
        all_sections.setdefault(label, value)
        key = heading_key(label)
        if key and key not in canonical:
            canonical[key] = value
    return canonical, all_sections


def meta(soup: BeautifulSoup, attr: str, value: str) -> str:
    node = soup.find("meta", attrs={attr: value})
    return norm(node.get("content", "")) if isinstance(node, Tag) else ""


def parse_project(url: str, text: str, source_page: int, gallery: dict[str, Any]) -> dict[str, Any]:
    soup = BeautifulSoup(text, "lxml")
    h1 = soup.find("h1")
    title = norm(h1.get_text(" ", strip=True)) if isinstance(h1, Tag) else ""
    if not title:
        title = meta(soup, "property", "og:title")
    title = re.sub(r"\s*\|\s*Devpost\s*$", "", title, flags=re.I).strip()
    description = meta(soup, "property", "og:description") or meta(soup, "name", "description")
    canonical, all_sections = extract_sections(soup)
    main = soup.find("main")
    full_text = norm((main or soup).get_text(" ", strip=True))
    what = canonical.get("what_it_does", "")
    tagline = gallery.get("tagline_from_gallery", "") or description
    quality = "strong" if what else ("usable" if tagline or description else "weak")
    return {
        "url": url,
        "slug": url.rstrip("/").split("/")[-1],
        "source_gallery_page": source_page,
        "title": title or gallery.get("title_from_gallery", ""),
        "tagline": tagline,
        "description_meta": description,
        "what_it_does": what,
        "inspiration": canonical.get("inspiration", ""),
        "how_built": canonical.get("how_built", ""),
        "challenges": canonical.get("challenges", ""),
        "accomplishments": canonical.get("accomplishments", ""),
        "learned": canonical.get("learned", ""),
        "next": canonical.get("next", ""),
        "built_with": canonical.get("built_with", ""),
        "try_it_out": canonical.get("try_it_out", ""),
        "all_sections": all_sections,
        "full_text": full_text[:80000],
        "parse_quality": quality,
        "fetched_at": now(),
        "html_sha256": hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest(),
    }


def collect_gallery(client: Client, out: Path) -> list[dict[str, Any]]:
    pages_file = out / "gallery_pages.jsonl"
    latest: dict[int, dict[str, Any]] = {}
    for row in read_jsonl(pages_file):
        if row.get("page"):
            latest[int(row["page"])] = row
    for page in range(1, PAGE_COUNT + 1):
        if latest.get(page, {}).get("count_ok"):
            continue
        page_url = f"{GALLERY}?page={page}"
        best: dict[str, Any] | None = None
        for retry in range(3):
            result = client.get(page_url)
            if result.text:
                record = parse_gallery(page, page_url, result.text)
                record.update({"status_code": result.status, "attempts": result.attempts, "fetch_error": None})
            else:
                record = {
                    "page": page,
                    "url": page_url,
                    "fetched_at": now(),
                    "expected_count": LAST_PAGE_SIZE if page == PAGE_COUNT else PAGE_SIZE,
                    "project_count": 0,
                    "count_ok": False,
                    "projects": [],
                    "status_code": result.status,
                    "attempts": result.attempts,
                    "fetch_error": result.error,
                }
            append_jsonl(pages_file, record)
            best = record
            print(f"gallery {page:02d}/{PAGE_COUNT}: {record['project_count']}/{record['expected_count']}", flush=True)
            if record["count_ok"]:
                break
            time.sleep(2 + retry * 2)
        latest[page] = best or {}

    unique: dict[str, dict[str, Any]] = {}
    ordinal = 0
    for page in range(1, PAGE_COUNT + 1):
        for item in latest.get(page, {}).get("projects", []):
            if item["url"] not in unique:
                ordinal += 1
                unique[item["url"]] = {"ordinal": ordinal, "source_gallery_page": page, **item}
    index = list(unique.values())
    with (out / "gallery_index.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["ordinal", "source_gallery_page", "url", "title_from_gallery", "tagline_from_gallery"])
        writer.writeheader()
        writer.writerows(index)
    write_json(
        out / "gallery_summary.json",
        {
            "generated_at": now(),
            "expected_total": EXPECTED_TOTAL,
            "unique_project_urls": len(index),
            "problem_pages": [p for p in range(1, PAGE_COUNT + 1) if not latest.get(p, {}).get("count_ok")],
        },
    )
    return index


def collect_projects(client: Client, out: Path, index: list[dict[str, Any]]) -> None:
    projects_file = out / "projects.jsonl"
    latest = {row.get("url"): row for row in read_jsonl(projects_file) if row.get("url")}
    done = {url for url, row in latest.items() if not row.get("fetch_error")}
    for item in index:
        url = item["url"]
        if url in done:
            continue
        result = client.get(url)
        if result.text:
            record = parse_project(url, result.text, int(item["source_gallery_page"]), item)
            record.update({"http_status": result.status, "fetch_attempts": result.attempts, "fetch_error": None})
        else:
            record = {
                "url": url,
                "slug": url.rstrip("/").split("/")[-1],
                "source_gallery_page": item["source_gallery_page"],
                "title": item.get("title_from_gallery", ""),
                "tagline": item.get("tagline_from_gallery", ""),
                "parse_quality": "failed",
                "http_status": result.status,
                "fetch_attempts": result.attempts,
                "fetch_error": result.error,
                "fetched_at": now(),
            }
        append_jsonl(projects_file, record)
        latest[url] = record
        ordinal = item["ordinal"]
        print(f"project {ordinal:04d}/{len(index)}: {record.get('parse_quality')} {record.get('title') or record.get('slug')}", flush=True)
        write_json(
            out / "crawl_state.json",
            {
                "updated_at": now(),
                "gallery_urls": len(index),
                "latest_project_ordinal": ordinal,
                "successful_project_records": sum(1 for row in latest.values() if not row.get("fetch_error")),
                "failed_project_records": sum(1 for row in latest.values() if row.get("fetch_error")),
            },
        )

    failures = [row for row in latest.values() if row.get("fetch_error")]
    with (out / "failures.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        fields = ["url", "slug", "source_gallery_page", "http_status", "fetch_attempts", "fetch_error", "fetched_at"]
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(failures)


def finalize(out: Path, index: list[dict[str, Any]]) -> dict[str, Any]:
    latest = {row.get("url"): row for row in read_jsonl(out / "projects.jsonl") if row.get("url")}
    success = [row for row in latest.values() if not row.get("fetch_error")]
    failures = [row for row in latest.values() if row.get("fetch_error")]
    quality: dict[str, int] = {}
    for row in success:
        key = row.get("parse_quality", "unknown")
        quality[key] = quality.get(key, 0) + 1
    summary = {
        "generated_at": now(),
        "expected_total": EXPECTED_TOTAL,
        "unique_gallery_urls": len(index),
        "unique_project_records": len(latest),
        "successful_project_records": len(success),
        "failed_project_records": len(failures),
        "parse_quality_counts": quality,
        "complete": len(index) == EXPECTED_TOTAL and len(success) == EXPECTED_TOTAL and not failures,
    }
    write_json(out / "crawl_summary.json", summary)
    zip_path = out / "all_things_agentic_raw.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in ["gallery_pages.jsonl", "gallery_index.csv", "gallery_summary.json", "projects.jsonl", "failures.csv", "crawl_state.json", "crawl_summary.json"]:
            path = out / name
            if path.exists():
                archive.write(path, arcname=name)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="all_things_agentic_data")
    parser.add_argument("--delay", type=float, default=0.65)
    parser.add_argument("--timeout", type=float, default=35.0)
    parser.add_argument("--attempts", type=int, default=5)
    args = parser.parse_args()

    out = Path(args.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    client = Client(args.delay, args.timeout, args.attempts)
    index = collect_gallery(client, out)
    collect_projects(client, out, index)
    summary = finalize(out, index)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
