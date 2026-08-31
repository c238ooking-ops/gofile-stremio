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
    log("🌐 Launching Chromium to capture live browser request headers...")
    captured_headers = {}
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
            nonlocal auth_event
            # Intercept the exact request Gofile's frontend makes
            if "contents/" in request.url and not captured_headers:
                captured_headers.update(dict(request.headers))
                auth_event = True

        page.on("request", intercept_request)

        try:
            page.goto(ROOT_URL, wait_until="domcontentloaded", timeout=45000)
            # Wait up to 10 seconds for Gofile frontend to trigger its contents API call
            for _ in range(10):
                if auth_event:
                    break
                page.wait_for_timeout(1000)
        except Exception as e:
            log(f"Browser navigation notice: {e}")

        cookies = context.cookies()
        cookie_header = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        browser.close()

    if not captured_headers:
        log("⚠️ No request to /contents/ was intercepted. Falling back to default headers.")
        captured_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Origin": "https://gofile.io",
            "Referer": "https://gofile.io/"
        }

    if cookie_header:
        captured_headers["cookie"] = cookie_header

    log(f"🔑 Successfully captured active session headers (Total header keys: {len(captured_headers)})")

    # Set up session with the intercepted headers
    session = requests.Session()
    session.headers.update(captured_headers)

    folders_queue = deque([(ROOT_FOLDER_ID, "Root Folder")])
    visited = set()
    all_files = {}

    log("🚀 Crawling Gofile folder tree...")

    while folders_queue:
        folder_id, folder_name = folders_queue.popleft()
        if folder_id in visited:
            continue
        visited.add(folder_id)

        page_num = 1
        folder_files = 0

        while True:
            api_url = f"https://api.gofile.io/contents/{folder_id}?page={page_num}&pageSize=1000&sortField=createTime&sortDirection=-1"
            try:
                res = session.get(api_url, timeout=20).json()
                status = res.get("status")

                if status != "ok":
                    log(f"⚠️ Folder [{folder_name}] status: {status}")
                    break

                data = res.get("data", {})
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
            except Exception as e:
                log(f"❌ Error crawling {folder_id} page {page_num}: {e}")
                break

        log(f"📂 [{folder_name}] Found {folder_files} files.")

    file_list = list(all_files.values())
    log(f"🎉 Crawl finished: Extracted {len(file_list)} total playable files.")

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
