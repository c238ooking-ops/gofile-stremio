import json
import time
import sys
from playwright.sync_api import sync_playwright

ROOT_URL = "https://gofile.io/d/OBVVp1LI"
ROOT_FOLDER_ID = "OBVVp1LI"

def log(msg):
    print(msg, flush=True)

def scrape():
    log("🌐 Starting Chromium session...")
    
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

        log(f"📄 Navigating to {ROOT_URL}...")
        try:
            # Use domcontentloaded to prevent networkidle infinite hanging
            page.goto(ROOT_URL, wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(4000)
        except Exception as e:
            log(f"Navigation warning: {e}")

        log("🔑 Extracting browser session tokens and cookies...")
        auth_data = page.evaluate("""() => {
            let accToken = localStorage.getItem('accountToken') || localStorage.getItem('token') || '';
            let wt = localStorage.getItem('websiteToken') || '';
            return {
                token: accToken,
                wt: wt,
                cookie: document.cookie || ''
            };
        }""")

        cookies = context.cookies()
        if not auth_data["cookie"]:
            auth_data["cookie"] = "; ".join([f"{c['name']}={c['value']}" for c in cookies])

        log(f"✅ Auth ready. Cookie length: {len(auth_data['cookie'])}, AccToken: {bool(auth_data['token'])}")

        log("🚀 Crawling directory tree through browser session...")
        crawl_results = page.evaluate("""async (rootFolderId) => {
            let queue = [rootFolderId];
            let visited = new Set();
            let allFiles = [];
            let token = localStorage.getItem('accountToken') || localStorage.getItem('token') || '';
            let wt = localStorage.getItem('websiteToken') || '';

            // Get base request headers
            let customHeaders = {};
            if (token) customHeaders['Authorization'] = 'Bearer ' + token;
            if (wt) customHeaders['X-Website-Token'] = wt;

            while (queue.length > 0) {
                let currentFolder = queue.shift();
                if (visited.has(currentFolder)) continue;
                visited.add(currentFolder);

                let pageNum = 1;
                while (pageNum <= 20) {
                    let apiUrl = `https://api.gofile.io/contents/${currentFolder}?page=${pageNum}&pageSize=100&sortField=createTime&sortDirection=-1`;
                    if (wt) apiUrl += `&wt=${encodeURIComponent(wt)}`;

                    try {
                        let resp = await fetch(apiUrl, {
                            method: 'GET',
                            headers: customHeaders
                        });
                        let json = await resp.json();
                        
                        if (json.status !== 'ok' || !json.data || !json.data.children) {
                            break;
                        }

                        let children = json.data.children;
                        let count = 0;

                        for (let id in children) {
                            count++;
                            let item = children[id];
                            if (item.type === 'folder') {
                                let code = item.code || item.id || id;
                                if (!visited.has(code) && !queue.includes(code)) {
                                    queue.push(code);
                                }
                            } else {
                                let dlUrl = item.link || item.directDownload || item.downloadPage || '';
                                if (dlUrl) {
                                    allFiles.push({
                                        id: 'gofile:' + id,
                                        item_id: id,
                                        name: item.name || 'Untitled',
                                        url: dlUrl,
                                        size: item.size ? (item.size / (1024 * 1024)).toFixed(2) + ' MB' : 'Unknown'
                                    });
                                }
                            }
                        }

                        if (count === 0 || pageNum >= (json.data.totalChildrenPages || 1)) {
                            break;
                        }
                        pageNum++;
                    } catch (err) {
                        break;
                    }
                }
            }
            return allFiles;
        }""", ROOT_FOLDER_ID)

        browser.close()

    log(f"🎉 Crawl finished: Retrieved {len(crawl_results)} total items.")

    output_payload = {
        "auth": auth_data,
        "files": crawl_results,
        "updated_at": int(time.time())
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2)

    log("💾 Successfully updated data.json")

if __name__ == "__main__":
    scrape()
