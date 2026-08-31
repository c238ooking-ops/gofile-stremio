import json
import time
from playwright.sync_api import sync_playwright

ROOT_URL = "https://gofile.io/d/OBVVp1LI"

def scrape_via_browser_interception():
    print("🌐 Launching Chromium browser runner...")
    
    captured_data = {
        "auth": {},
        "files": {}
    }

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
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        def handle_response(response):
            # Intercept every content data payload returned to the webpage
            if "contents/" in response.url:
                try:
                    req_headers = dict(response.request.headers)
                    if req_headers and not captured_data["auth"]:
                        captured_data["auth"] = req_headers

                    res_json = response.json()
                    if res_json.get("status") == "ok":
                        children = res_json.get("data", {}).get("children", {})
                        for item_id, item in children.items():
                            if item.get("type") != "folder":
                                dl_url = item.get("link") or item.get("directDownload") or item.get("downloadPage")
                                size = item.get("size", 0)
                                size_mb = f"{(size / (1024 * 1024)):.2f} MB" if size else "Unknown"

                                if item_id not in captured_data["files"] and dl_url:
                                    captured_data["files"][item_id] = {
                                        "id": f"gofile:{item_id}",
                                        "item_id": item_id,
                                        "name": item.get("name", "Untitled"),
                                        "url": dl_url,
                                        "size": size_mb
                                    }
                except Exception:
                    pass

        page.on("response", handle_response)

        print("📄 Navigating to root folder and waiting for Gofile DOM to hydrate...")
        page.goto(ROOT_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(5000)

        # Scroll down and trigger pagination if folder has many items
        for _ in range(5):
            page.mouse.wheel(0, 5000)
            page.wait_for_timeout(1500)

        # Find any subfolder links rendered on screen and click into them
        subfolders = page.locator("a[href*='/d/']").all()
        subfolder_urls = []
        for sf in subfolders:
            href = sf.get_attribute("href")
            if href and href not in subfolder_urls and href != "/d/OBVVp1LI" and ROOT_URL not in href:
                subfolder_urls.append(href if href.startswith("http") else f"https://gofile.io{href}")

        print(f"📂 Found {len(subfolder_urls)} subfolder(s). Navigating into each...")

        for s_url in subfolder_urls:
            try:
                print(f"➜ Opening subfolder: {s_url}")
                page.goto(s_url, wait_until="networkidle", timeout=40000)
                page.wait_for_timeout(3000)
                for _ in range(4):
                    page.mouse.wheel(0, 5000)
                    page.wait_for_timeout(1000)
            except Exception as e:
                print(f"Subfolder notice: {e}")

        cookies = context.cookies()
        cookie_header = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
        
        token = page.evaluate("() => localStorage.getItem('accountToken') || localStorage.getItem('token') || ''")

        browser.close()

    file_list = list(captured_data["files"].values())
    print(f"🎉 Crawl finished: Intercepted {len(file_list)} total playable files.")

    output = {
        "auth": {
            "token": token,
            "cookie": cookie_header,
            "headers": captured_data["auth"]
        },
        "files": file_list,
        "updated_at": int(time.time())
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

if __name__ == "__main__":
    scrape_via_browser_interception()
