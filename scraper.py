import json
import time
import requests
from collections import deque
from playwright.sync_api import sync_playwright

ROOT_URL = "https://gofile.io/d/OBVVp1LI"
ROOT_FOLDER_ID = "OBVVp1LI"

def parse_cookie_token(cookie_str):
    for part in cookie_str.split(";"):
        part = part.strip()
        if part.startswith("accountToken="):
            return part.split("=", 1)[1]
    return ""

def scrape():
    print("🌐 Launching Chromium on GitHub runner...")
    auth_data = {}

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

        print("📄 Loading Gofile page to generate active guest session...")
        page.goto(ROOT_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(4000)

        # Retrieve cookies and local storage tokens
        cookies = context.cookies()
        cookie_header = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        
        token = page.evaluate("""() => {
            return localStorage.getItem('accountToken') || localStorage.getItem('token') || '';
        }""")

        if not token:
            token = parse_cookie_token(cookie_header)

        auth_data = {
            "token": token,
            "cookie": cookie_header
        }
        print(f"🔑 Captured token: {token[:8]}... (Valid: {bool(token)})")

        browser.close()

    # Now use Python requests with the captured token to crawl all folders
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Origin": "https://gofile.io",
        "Referer": "https://gofile.io/",
        "Authorization": f"Bearer {auth_data['token']}",
        "Cookie": auth_data["cookie"]
    })

    folders_queue = deque([(ROOT_FOLDER_ID, "Root Folder")])
    visited = set()
    all_files = {}

    print("🚀 Crawling all folders and files...")

    while folders_queue:
        folder_id, folder_name = folders_queue.popleft()
        if folder_id in visited:
            continue
        visited.add(folder_id)

        page_num = 1
        seen_in_folder = 0

        while True:
            api_url = f"https://api.gofile.io/contents/{folder_id}?page={page_num}&pageSize=100&sortField=createTime&sortDirection=-1"
            try:
                res = session.get(api_url, timeout=20).json()
                if res.get("status") != "ok":
                    print(f"⚠️ API status: {res.get('status')} on folder {folder_id}")
                    break

                data = res.get("data", {})
                children = data.get("children", {})
                if not children:
                    break

                for item_id, item in children.items():
                    seen_in_folder += 1
                    item_type = item.get("type", "")

                    if item_type == "folder":
                        code = item.get("code") or item.get("id") or item_id
                        if code not in visited and all(code != f[0] for f in folders_queue):
                            folders_queue.append((code, item.get("name", "")))
                    else:
                        dl_url = item.get("link") or item.get("directDownload") or item.get("downloadPage")
                        size = item.get("size", 0)
                        size_mb = f"{(size / (1024 * 1024)):.2f} MB" if size else "Unknown"

                        if item_id not in all_files and dl_url:
                            all_files[item_id] = {
                                "id": f"gofile:{item_id}",
                                "item_id": item_id,
                                "name": item.get("name", "Untitled"),
                                "url": dl_url,
                                "size": size_mb
                            }

                total_pages = data.get("totalChildrenPages", 1)
                if page_num >= total_pages or len(children) == 0:
                    break
                page_num += 1
            except Exception as e:
                print(f"❌ Crawl error on folder {folder_id}: {e}")
                break

        print(f"📂 [{folder_name}] Indexed {seen_in_folder} total items.")

    output = {
        "auth": auth_data,
        "files": list(all_files.values()),
        "updated_at": int(time.time())
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"🎉 Successfully saved {len(output['files'])} files to data.json")

if __name__ == "__main__":
    scrape()
