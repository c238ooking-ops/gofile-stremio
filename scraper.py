import json
import time
import requests
from collections import deque
from playwright.sync_api import sync_playwright

ROOT_URL = "https://gofile.io/d/OBVVp1LI"
ROOT_FOLDER_ID = "OBVVp1LI"

def scrape():
    print("🌐 Launching Playwright on GitHub runner...")
    captured = {"headers": {}}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        def intercept(request):
            if "contents/" in request.url and not captured["headers"]:
                captured["headers"] = dict(request.headers)

        page.on("request", intercept)
        page.goto(ROOT_URL, wait_until="networkidle", timeout=60000)
        time.sleep(3)
        browser.close()

    headers = captured["headers"]
    session = requests.Session()
    session.headers.update(headers)

    folders_queue = deque([(ROOT_FOLDER_ID, "Root")])
    visited = set()
    all_files = {}

    while folders_queue:
        folder_id, folder_name = folders_queue.popleft()
        if folder_id in visited:
            continue
        visited.add(folder_id)

        page_num = 1
        while True:
            api_url = f"https://api.gofile.io/contents/{folder_id}?page={page_num}&pageSize=1000&sortField=createTime&sortDirection=-1"
            try:
                res = session.get(api_url, timeout=20).json()
                if res.get("status") != "ok":
                    break
                data = res.get("data", {})
                children = data.get("children", {})
                if not children:
                    break

                for item_id, item in children.items():
                    if item.get("type") == "folder":
                        code = item.get("code") or item.get("id") or item_id
                        if code not in visited:
                            folders_queue.append((code, item.get("name", "")))
                    else:
                        dl_url = item.get("link") or item.get("directDownload")
                        size = item.get("size", 0)
                        if dl_url:
                            all_files[item_id] = {
                                "id": f"gofile:{item_id}",
                                "name": item.get("name", "Untitled"),
                                "url": dl_url,
                                "size": f"{(size / (1024 * 1024)):.2f} MB"
                            }

                if page_num >= data.get("totalChildrenPages", 1):
                    break
                page_num += 1
            except Exception as e:
                print(f"Error reading {folder_id}: {e}")
                break

    output = {
        "headers": headers,
        "files": list(all_files.values()),
        "updated_at": int(time.time())
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"✅ Extracted {len(output['files'])} files successfully.")

if __name__ == "__main__":
    scrape()
