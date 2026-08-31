import json
import time
import requests
from collections import deque
from playwright.sync_api import sync_playwright

ROOT_URL = "https://gofile.io/d/OBVVp1LI"
ROOT_FOLDER_ID = "OBVVp1LI"

def log(msg):
    print(msg, flush=True)

def scrape():
    log("🌐 Launching Chromium to capture live browser request headers & tokens...")
    captured_headers = {}
    website_token = ""
    auth_event = False

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu"
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )
        page = context.new_page()

        def intercept_request(request):
            nonlocal auth_event, website_token
            if "contents/" in request.url:
                if not captured_headers:
                    captured_headers.update(dict(request.headers))
                # Extract dynamic wt token from query parameters if present
                if "wt=" in request.url:
                    parts = request.url.split("wt=")
                    if len(parts) > 1:
                        website_token = parts[1].split("&")[0]
                auth_event = True

        page.on("request", intercept_request)

        try:
            page.goto(ROOT_URL, wait_until="domcontentloaded", timeout=45000)
            for _ in range(10):
                if auth_event:
                    break
                page.wait_for_timeout(1000)
        except Exception as e:
            log(f"Browser navigation notice: {e}")

        cookies = context.cookies()
        cookie_header = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        
        # Fallback extraction for websiteToken
        if not website_token:
            website_token = page.evaluate("() => localStorage.getItem('websiteToken') || ''")

        browser.close()

    if not captured_headers:
        captured_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Origin": "https://gofile.io",
            "Referer": "https://gofile.io/"
        }

    if cookie_header:
        captured_headers["cookie"] = cookie_header

    log(f"🔑 Auth captured. WT token: {bool(website_token)}, Total headers: {len(captured_headers)}")

    session = requests.Session()
    session.headers.update(captured_headers)

    folders_queue = deque([(ROOT_FOLDER_ID, "Root Folder")])
    visited = set()
    all_files = {}

    log("🚀 Crawling complete Gofile directory tree with retry backoff...")

    while folders_queue:
        folder_id, folder_name = folders_queue.popleft()
        if folder_id in visited:
            continue
        visited.add(folder_id)

        page_num = 1
        folder_files = 0
        folder_subfolders = 0

        while True:
            api_url = f"https://api.gofile.io/contents/{folder_id}?page={page_num}&pageSize=1000&sortField=createTime&sortDirection=-1"
            if website_token:
                api_url += f"&wt={website_token}"

            res_data = None
            # Retry loop with progressive backoff for rate-limits
            for attempt in range(4):
                try:
                    time.sleep(0.5)  # Safe spacing between calls to prevent rate limits
                    res = session.get(api_url, timeout=25).json()
                    status = res.get("status")

                    if status == "ok":
                        res_data = res
                        break
                    elif status in ["error-rateLimit", "error-auth", "error-token"]:
                        wait_sec = (attempt + 1) * 3
                        log(f"⏳ [{status}] on '{folder_name}'. Backing off {wait_sec}s...")
                        time.sleep(wait_sec)
                    else:
                        log(f"⚠️ Folder [{folder_name}] status: {status}")
                        break
                except Exception as err:
                    time.sleep(2)

            if not res_data:
                break

            data = res_data.get("data", {})
            children = data.get("children", {})
            if not children:
                break

            for item_id, item in children.items():
                item_type = item.get("type", "")
                item_name = item.get("name", "Untitled")

                if item_type == "folder":
                    code = item.get("code") or item.get("id") or item_id
                    if code not in visited and all(code != f[0] for f in folders_queue):
                        folders_queue.append((code, item_name))
                        folder_subfolders += 1
                else:
                    dl_url = item.get("link") or item.get("directDownload") or item.get("downloadPage")
                    size = item.get("size", 0)
                    size_mb = f"{(size / (1024 * 1024)):.2f} MB" if size else "Unknown"

                    if item_id not in all_files and dl_url:
                        all_files[item_id] = {
                            "id": f"gofile:{item_id}",
                            "item_id": item_id,
                            "name": item_name,
                            "url": dl_url,
                            "size": size_mb
                        }
                        folder_files += 1

            total_pages = data.get("totalChildrenPages", 1)
            if page_num >= total_pages or len(children) == 0:
                break
            page_num += 1

        log(f"📂 [{folder_name}] ➜ {folder_files} files, {folder_subfolders} subfolders discovered.")

    file_list = list(all_files.values())
    log(f"🎉 Crawl finished: Extracted {len(file_list)} total playable files across {len(visited)} folders.")

    output_payload = {
        "headers": captured_headers,
        "files": file_list,
        "updated_at": int(time.time())
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2)

    log("💾 Successfully updated data.json")

if __name__ == "__main__":
    scrape()
