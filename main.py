import os
import time
import threading
from collections import deque
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from playwright.sync_api import sync_playwright

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT_URL = "https://gofile.io/d/OBVVp1LI"
cached_videos = []
last_scan_time = 0
is_crawling = False

def scrape_gofile_dom():
    """Extracts directory files straight from the rendered browser page."""
    global cached_videos, last_scan_time, is_crawling
    if is_crawling:
        return cached_videos

    is_crawling = True
    found_videos = []
    print("🌐 Launching Chromium to scrape rendered Gofile DOM...")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--single-process"
                ]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720}
            )
            page = context.new_page()

            page.goto(ROOT_URL, wait_until="networkidle", timeout=60000)
            # Give Gofile's UI 4 seconds to render items
            page.wait_for_timeout(4000)

            # Extract rendered file items directly from Gofile's global state or DOM links
            extracted = page.evaluate("""() => {
                let items = [];
                // 1. Try extracting from Gofile frontend memory state
                if (window.app && window.app.currentFolder && window.app.currentFolder.children) {
                    for (let id in window.app.currentFolder.children) {
                        let c = window.app.currentFolder.children[id];
                        items.push({
                            id: id,
                            name: c.name || "Untitled",
                            url: c.link || c.directDownload || "",
                            size: c.size ? (c.size / (1024 * 1024)).toFixed(2) + " MB" : "Unknown"
                        });
                    }
                }
                
                // 2. Fallback: Parse anchor elements if memory state is obfuscated
                if (items.length === 0) {
                    document.querySelectorAll("a[href*='srv-file'], a[id^='content_']").forEach((el, idx) => {
                        let href = el.getAttribute("href");
                        let name = el.innerText.trim() || ("File " + (idx + 1));
                        if (href && href.startsWith("http")) {
                            items.push({
                                id: "item_" + idx,
                                name: name,
                                url: href,
                                size: "Media File"
                            });
                        }
                    });
                }
                return items;
            }""")

            browser.close()

            if extracted:
                for item in extracted:
                    if item.get("url"):
                        found_videos.append({
                            "id": f"gofile:{item['id']}",
                            "name": item["name"],
                            "url": item["url"],
                            "size": item["size"]
                        })
                print(f"🎉 Successfully extracted {len(found_videos)} files from Gofile page.")

    except Exception as e:
        print(f"❌ Scraping error: {e}")
    finally:
        is_crawling = False

    if found_videos:
        cached_videos = found_videos
        last_scan_time = time.time()

    return cached_videos

# Support both GET and HEAD on root for Render's health checks
@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return RedirectResponse(url="/manifest.json")

@app.on_event("startup")
def on_startup():
    threading.Thread(target=scrape_gofile_dom, daemon=True).start()

@app.get("/manifest.json")
def get_manifest():
    return {
        "id": "community.gofile.streamer",
        "version": "1.0.0",
        "name": "Gofile Public Streamer",
        "description": "Play media files directly from public Gofile folder 24/7",
        "resources": ["catalog", "meta", "stream"],
        "types": ["other", "movie"],
        "catalogs": [
            {
                "type": "other",
                "id": "gofile_catalog",
                "name": "Gofile Library",
                "extra": [{"name": "search", "isRequired": False}]
            }
        ]
    }

@app.get("/catalog/other/gofile_catalog.json")
@app.get("/catalog/other/gofile_catalog/search={search_query}.json")
def get_catalog(search_query: str = None):
    videos = cached_videos or scrape_gofile_dom()
    metas = []
    for vid in videos:
        if search_query and search_query.lower() not in vid["name"].lower():
            continue
        metas.append({
            "id": vid["id"],
            "name": vid["name"],
            "type": "other",
            "poster": "https://gofile.io/dist/img/logo-small.png",
            "description": f"Size: {vid['size']}"
        })
    return {"metas": metas}

@app.get("/meta/other/{video_id}.json")
def get_meta(video_id: str):
    videos = cached_videos or scrape_gofile_dom()
    vid = next((v for v in videos if v["id"] == video_id), None)
    return {
        "meta": {
            "id": video_id,
            "name": vid["name"] if vid else "Video",
            "type": "other",
            "background": "https://gofile.io/dist/img/logo-small.png"
        }
    }

@app.get("/stream/other/{video_id}.json")
def get_stream(video_id: str):
    videos = cached_videos or scrape_gofile_dom()
    vid = next((v for v in videos if v["id"] == video_id), None)
    if not vid:
        raise HTTPException(status_code=404, detail="Stream not found")
    return {
        "streams": [
            {
                "title": "Direct Stream",
                "url": vid["url"]
            }
        ]
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7000))
    uvicorn.run(app, host="0.0.0.0", port=port)
