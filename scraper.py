#!/usr/bin/env python3
"""
Scraper fuer "Anderswo auf der Domaene".

Quellen:
  - GloPhi: REST API (The Events Calendar / tribe_events)
  - Institut fuer Philosophie: HTML der .news article-Blocks

Output: events.json im Repo-Root.

Fail-soft:
  - Liefert eine Quelle 0 Ergebnisse, aber im alten JSON stehen welche,
    werden die alten behalten. So loescht ein HTML-Umbau beim Institut
    nicht die GloPhi-Eintraege (und umgekehrt).
  - Hartes Fail (Exit 1) nur wenn beide Quellen explodieren.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# -------------------------------------------------------------------------
HEADERS = {
    "User-Agent": "domaene-philosophiert-events/1.0 "
                  "(+https://institut-fur-philosophie-hildesheim.ghost.io/)"
}
TIMEOUT = 30
OUT_FILE = pathlib.Path(__file__).resolve().parent / "events.json"

# show items up to 30 days in the past, and everything in the future
PAST_CUTOFF_DAYS = 30

GERMAN_MONTHS = {
    "januar": 1, "februar": 2, "maerz": 3, "märz": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9,
    "oktober": 10, "november": 11, "dezember": 12,
}

# -------------------------------------------------------------------------
@dataclass
class Event:
    date: str             # humanly-formatted date shown to the reader
    title: str
    location: str
    url: str
    source: str           # "glophi" | "institut"
    sort_key: str         # ISO date for sorting (YYYY-MM-DD), "9999-12-31" if unknown


# ========== GLOPHI (JSON API) ============================================
def scrape_glophi() -> list[Event]:
    """
    Uses The Events Calendar REST API.
    Endpoint returns a well-structured JSON:
      events[].start_date, end_date, title, url, venue.venue, categories[].name
    """
    today = date.today().isoformat()
    api = (
        "https://www.uni-hildesheim.de/glophi/wp-json/tribe/events/v1/events"
        f"?per_page=50&start_date={today}"
    )
    r = requests.get(api, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    events: list[Event] = []
    for e in data.get("events", []):
        start = e.get("start_date", "")          # "2026-04-28 14:00:00"
        end = e.get("end_date", "")
        title = (e.get("title") or "").strip()
        url = e.get("url") or "https://www.uni-hildesheim.de/glophi/"
        venue = (e.get("venue") or {}).get("venue") or ""
        cats = [c.get("name") for c in (e.get("categories") or []) if c.get("name")]
        cat_label = cats[0] if cats else ""

        sort_iso = (start or "")[:10] or "9999-12-31"
        date_str = _format_date_range(start, end)
        location_parts = [p for p in ["GloPhi", cat_label, venue] if p]
        location = " · ".join(location_parts[:2])  # keep it short
        if not title:
            continue
        events.append(Event(
            date=date_str, title=title, location=location,
            url=url, source="glophi", sort_key=sort_iso,
        ))
    return events


def _format_date_range(start: str, end: str) -> str:
    """'2026-04-28 14:00' + '2026-04-28 16:00' -> '28.04.2026'
       Multi-day -> '05.-06.10.2026'."""
    try:
        s = datetime.fromisoformat(start).date()
    except Exception:
        return ""
    try:
        e = datetime.fromisoformat(end).date()
    except Exception:
        e = s
    if e == s or not end:
        return s.strftime("%d.%m.%Y")
    if s.year == e.year and s.month == e.month:
        return f"{s.day:02d}.–{e.day:02d}.{e.month:02d}.{e.year}"
    return f"{s.strftime('%d.%m.%Y')} – {e.strftime('%d.%m.%Y')}"


# ========== INSTITUT (HTML) ==============================================
def scrape_institut() -> list[Event]:
    base = "https://www.uni-hildesheim.de/fb2/institute/philosophie/"
    r = requests.get(base, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    events: list[Event] = []

    # Each news item is an <article> inside .news
    for art in soup.select(".news article"):
        a = art.find("a", href=True)
        if not a:
            continue
        url = urljoin(base, a["href"])
        text = art.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text)
        # Pattern: "DD Month YYYY Title..."
        m = re.match(
            r"^(\d{1,2})\s+(Januar|Februar|M[aä]rz|April|Mai|Juni|Juli|"
            r"August|September|Oktober|November|Dezember)\s+(\d{4})\s+(.+)$",
            text, flags=re.IGNORECASE,
        )
        if not m:
            continue
        day, month_name, year, rest = m.groups()
        month = GERMAN_MONTHS.get(month_name.lower(), 0)
        if not month:
            continue
        title = rest.strip()
        # Dates sometimes appear inside title like "(Workshop 10.-13.03.2026)"
        evt_date_match = re.search(
            r"(\d{1,2})\.\s*[-–]\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", title
        ) or re.search(
            r"(\d{1,2})\.(\d{1,2})\.(\d{4})", title
        )
        try:
            iso_news = datetime(int(year), month, int(day)).date().isoformat()
        except ValueError:
            iso_news = "9999-12-31"

        if evt_date_match and len(evt_date_match.groups()) == 4:
            d1, d2, m_, y_ = evt_date_match.groups()
            date_str = f"{int(d1):02d}.–{int(d2):02d}.{int(m_):02d}.{y_}"
            try:
                iso_event = datetime(int(y_), int(m_), int(d1)).date().isoformat()
            except ValueError:
                iso_event = iso_news
            sort_key = iso_event
        elif evt_date_match and len(evt_date_match.groups()) == 3:
            d1, m_, y_ = evt_date_match.groups()
            date_str = f"{int(d1):02d}.{int(m_):02d}.{y_}"
            try:
                iso_event = datetime(int(y_), int(m_), int(d1)).date().isoformat()
            except ValueError:
                iso_event = iso_news
            sort_key = iso_event
        else:
            date_str = f"{int(day):02d}. {month_name.capitalize()} {year}"
            sort_key = iso_news

        events.append(Event(
            date=date_str, title=title, location="Institut für Philosophie",
            url=url, source="institut", sort_key=sort_key,
        ))
    return events


# ========== MAIN =========================================================
def load_existing() -> list[dict]:
    if not OUT_FILE.exists():
        return []
    try:
        return json.loads(OUT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def run_one(name: str, fn) -> tuple[list[Event], Optional[str]]:
    try:
        got = fn()
        print(f"[{name}] ok — {len(got)} entries", file=sys.stderr)
        return got, None
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        print(f"[{name}] FAILED — {msg}", file=sys.stderr)
        return [], msg


def main() -> int:
    existing = load_existing()
    existing_by_source: dict[str, list[dict]] = {"glophi": [], "institut": []}
    for e in existing:
        existing_by_source.setdefault(e.get("source", ""), []).append(e)

    errors: list[str] = []
    combined: list[dict] = []

    for name, fn in [("institut", scrape_institut), ("glophi", scrape_glophi)]:
        got, err = run_one(name, fn)
        if err:
            errors.append(f"{name}: {err}")
            # keep old entries for this source
            combined.extend(existing_by_source.get(name, []))
        elif not got and existing_by_source.get(name):
            print(
                f"[{name}] 0 entries but {len(existing_by_source[name])} existed — "
                f"keeping old (fail-soft)",
                file=sys.stderr,
            )
            combined.extend(existing_by_source[name])
        else:
            combined.extend([asdict(e) for e in got])

    # Drop too-old events (if sort_key parseable as ISO date)
    cutoff = (date.today() - timedelta(days=PAST_CUTOFF_DAYS)).isoformat()
    combined = [
        e for e in combined
        if not re.match(r"\d{4}-\d{2}-\d{2}", e.get("sort_key", "")) or
        e["sort_key"] >= cutoff
    ]

    # Sort ascending by sort_key (ISO), unknown dates to the end
    combined.sort(key=lambda e: e.get("sort_key", "9999-12-31"))

    # Limit
    combined = combined[:20]

    # If BOTH sources failed -> hard fail, don't rewrite the file
    if len(errors) >= 2:
        print("Both sources failed; keeping old events.json untouched.", file=sys.stderr)
        return 1

    # Write (only overwrite if we actually have something)
    if not combined:
        print("Nothing to write; aborting.", file=sys.stderr)
        return 1

    OUT_FILE.write_text(
        json.dumps(combined, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(combined)} events -> {OUT_FILE}")
    if errors:
        # One source soft-failed; exit 0 so partial update commits.
        print("Partial-fail summary:\n" + "\n".join(errors), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
