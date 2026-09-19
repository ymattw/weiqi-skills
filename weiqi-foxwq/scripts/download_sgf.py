#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fox Weiqi game record auto-download script
Automatically downloads game records for a specified date
"""

import os
import re
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import urljoin
from contextlib import contextmanager
from collections import OrderedDict

# HTML parsing library
try:
    from bs4 import BeautifulSoup

    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    print("⚠️  BeautifulSoup is not installed; falling back to regex parsing")

# Try to import requests
try:
    import requests

    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    print("⚠️  requests is not installed, please run: pip3 install requests")


# ===== Performance timing utility =====
class PerformanceTimer:
    """Performance timer - tracks the elapsed time of each step"""

    def __init__(self):
        self.timings = OrderedDict()
        self.start_time = None
        self.step_start = None

    def start(self):
        """Start the overall timer"""
        self.start_time = time.time()
        return self

    @contextmanager
    def step(self, name):
        """Context manager - times a single step"""
        step_start = time.time()
        try:
            yield self
        finally:
            elapsed = time.time() - step_start
            self.timings[name] = elapsed

    def get_total(self):
        """Get the total elapsed time"""
        if self.start_time:
            return time.time() - self.start_time
        return 0

    def format_report(self):
        """Format the timing report"""
        lines = []
        lines.append("\n" + "=" * 50)
        lines.append("⏱️  Performance Timing Report")
        lines.append("=" * 50)

        total_step_time = 0
        for name, elapsed in self.timings.items():
            total_step_time += elapsed
            lines.append(f"  {name:20s} : {elapsed:>8.3f}s")

        lines.append("-" * 50)
        lines.append(f"  {'Step total':20s} : {total_step_time:>8.3f}s")
        lines.append(f"  {'Total elapsed':20s} : {self.get_total():>8.3f}s")
        lines.append("=" * 50)
        return "\n".join(lines)


# Global timer instance
timer = PerformanceTimer()

# Configuration
WORK_DIR = os.environ.get("FOXWQ_DOWNLOAD_DIR", "/tmp/foxwq_downloads")
BASE_URL = "https://www.foxwq.com"
LIST_URL = "https://www.foxwq.com/qipu.html"


def fetch_url(url):
    """Fetch URL content (using the requests library)"""
    if not REQUESTS_AVAILABLE:
        print(f"❌ requests is not installed, cannot fetch: {url}")
        return None

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.text
    except Exception as e:
        print(f"❌ Fetch failed {url}: {e}")
        return None


def extract_qipu_links(html, target_date):
    """Extract game record links for the target date from the HTML"""
    links = []

    if BS4_AVAILABLE:
        # Use BeautifulSoup for efficient parsing
        soup = BeautifulSoup(html, "lxml")

        # Find all table rows
        for row in soup.find_all("tr"):
            # Get the date cell
            date_cells = row.find_all("td")
            if len(date_cells) < 2:
                continue

            # Check whether the date matches (the last td is usually the date)
            date_text = date_cells[-1].get_text(strip=True)
            if not date_text.startswith(target_date):
                continue

            # Find the link and title
            link_tag = row.find("a", href=re.compile(r"/qipu/newlist/id/\d+\.html"))
            if not link_tag:
                continue

            # Extract the title (from the h4 tag or the anchor text)
            title_tag = link_tag.find("h4")
            if title_tag:
                title = title_tag.get_text(strip=True)
            else:
                title = link_tag.get_text(strip=True)

            # Clean up the title
            title = title.replace("\n", " ").replace("\r", "").replace("&nbsp;", " ")

            full_url = urljoin(BASE_URL, link_tag["href"])
            links.append({"title": title, "url": full_url, "date": target_date})
    else:
        # Fallback: use regex parsing (slower)
        pattern = (
            r'<tr[^>]*>.*?<a[^>]*href="(/qipu/newlist/id/\d+\.html)"[^>]*>.*?<h4[^>]*>(.*?)</h4>.*?</td>.*?<td[^>]*>'
            + re.escape(target_date)
            + r"[^<]*</td>.*?</tr>"
        )
        matches = re.findall(pattern, html, re.DOTALL | re.IGNORECASE)

        for path, title in matches:
            full_url = urljoin(BASE_URL, path)
            clean_title = re.sub(r"<[^>]+>", "", title).strip()
            clean_title = clean_title.replace("&nbsp;", " ")
            links.append({"title": clean_title, "url": full_url, "date": target_date})

    return links


def extract_sgf(html):
    """Extract the SGF-format game record from the HTML"""
    # Find the SGF start position
    sgf_start = html.find("(;GM[1]FF[4]")
    if sgf_start == -1:
        return None

    # From the SGF start, find the position of the first HTML tag
    # The SGF content ends before the first HTML tag
    html_tag_match = re.search(r"</?[a-zA-Z][^>]*>", html[sgf_start:])

    if html_tag_match:
        # Extract the SGF content (from the start to before the first HTML tag)
        sgf_end = sgf_start + html_tag_match.start()
        sgf = html[sgf_start:sgf_end]
        # Remove trailing whitespace
        sgf = sgf.rstrip()
        return sgf
    else:
        # If no HTML tag is found, use the original logic (fallback)
        match = re.search(r"\(;GM\[1\]FF\[4\].*?\)\s*\)\s*\)", html, re.DOTALL)
        if match:
            sgf = match.group(0)
            sgf = re.sub(r"\n\s*<<<END_EXTERNAL.*", "", sgf)
            return sgf

    return None


def download_qipu(link_info, save_dir):
    """Download a single game record"""
    print(f"📥 Downloading: {link_info['title']}")

    html = fetch_url(link_info["url"])
    if not html:
        return None

    sgf = extract_sgf(html)
    if not sgf:
        print(f"  ⚠️ Could not extract SGF content {link_info['url']}")
        return None

    # Generate the file name (remove the "Jueyi commentary" suffix)
    title_clean = (
        link_info["title"].replace("绝艺讲解", "").replace("<", "").replace(">", "")
    )
    safe_title = re.sub(r"[^\w\u4e00-\u9fff]", "_", title_clean)[:50]
    # Extract the ID from the URL
    match = re.search(r"/id/(\d+)", link_info["url"])
    file_id = match.group(1) if match else datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"{file_id}_{safe_title}.sgf"
    filepath = os.path.join(save_dir, filename)

    # Save the file
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(sgf)

    print(f"  ✅ Saved: {filename}")
    return {"filename": filename, "title": link_info["title"], "path": filepath}


def print_report(target_date, success_list, failed_list, timer=None):
    """Print the download report"""
    success_count = len(success_list)
    failed_count = len(failed_list)

    print(f"\n{'='*50}")
    print("🎯 Fox Weiqi Game Download Report")
    print(f"{'='*50}")
    print(f"\nDownload time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Target date: {target_date}")
    print(f"\n{'='*50}")
    print("📊 Download Statistics")
    print(f"{'='*50}")
    print(f"\n✅ Successful: {success_count} games")
    print(f"❌ Failed: {failed_count} games")

    if success_count > 0:
        print(f"\n{'='*50}")
        print("📁 Downloaded game records")
        print(f"{'='*50}")
        for item in success_list:
            print(f"\n• {item['title']}")
            print(f"  File: {item['filename']}")

    # Print the performance report
    if timer:
        print(timer.format_report())


def main():
    # Get yesterday's date
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    target_date = sys.argv[1] if len(sys.argv) > 1 else yesterday

    print("🎯 Fox Weiqi Game Record Download")
    print(f"Target date: {target_date}")
    print(f"{'='*50}")

    # Start performance timing
    timer.start()

    # Create save directory
    save_dir = os.path.join(WORK_DIR, target_date)
    with timer.step("Create directory"):
        os.makedirs(save_dir, exist_ok=True)
    print(f"Save path: {save_dir}")
    print()

    # Fetch the list page
    print("📄 Fetching game record list...")
    with timer.step("Fetch list page"):
        html = fetch_url(LIST_URL)
    if not html:
        print("❌ Could not fetch the list page")
        print(timer.format_report())
        return

    # Extract links
    with timer.step("Parse game record links"):
        links = extract_qipu_links(html, target_date)
    print(f"✅ Found {len(links)} game records for {target_date}")
    print()

    if not links:
        print("📋 No new game records today")
        print(timer.format_report())
        return

    # Download game records
    success_list = []
    failed_list = []

    with timer.step(f"Download {len(links)} game records"):
        for link in links:
            result = download_qipu(link, save_dir)
            if result:
                success_list.append(result)
            else:
                failed_list.append(link["title"])

    print()
    print_report(target_date, success_list, failed_list, timer)


if __name__ == "__main__":
    main()
