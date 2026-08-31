import os
import time
import uvicorn
from collections import deque
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from playwright.sync_api import sync_playwright
import requests

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

class SessionManager:
    def __init__(self, root_url):
        self.root_url = root_url
        self.session = requests.Session()
        self.last_auth_time = 0
        self.refresh_credentials()

    def refresh_credentials(self):
        print("Launching Chromium to capture fresh credentials...")
        captured = {"headers": {}}
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            def intercept_request(request):
                if "contents/" in request.url:
                    captured["headers"] = dict(request.headers)

            page.on("request", intercept_request)
            try:
                page.goto(self.root_url, wait_until="networkidle", timeout=45000)
                time.sleep(2)
            except Exception as e:
                print(f"Playwright navigation notice: {e}")
            browser.close()

        self.session.headers.clear()
        self.session.headers.update(captured["headers"])
        self.last_auth_time = time.time()
        print("Fresh session credentials loaded.")

    def ensure_fresh(self):
        if time.time() - self.last_auth_time > 900:
            self.refresh_credentials()

def crawl_gofile():
    global cached_videos, last_scan_time
    if cached_videos and (time.time() - last_scan_time < 1800):
        return cached_videos

    session_mgr = SessionManager(ROOT_URL)
    folders_queue = deque([("OBVVp1LI", "Root Folder")])
    visited_folders = set()
    found_videos = []

    while folders_queue:
        current_folder_id, _ = folders_queue.popleft()
        if current_folder_id in visited_folders:
            continue
        visited_folders.add(current_folder_id)

        api_url = f"https://api.gofile.io/contents/{current_folder_id}?page=1&pageSize=100"
        session_mgr.ensure_fresh()
        
        try:
            res = session_mgr.session.get(api_url, timeout=20).json()
            if res.get("status") == "ok":
                children = res.get("data", {}).get("children", {})
                for item_id, item in children.items():
                    if item.get("type") == "folder":
                        code = item.get("code") or item.get("id") or item_id
                        folders_queue.append((code, item.get("name", "")))
                    else:
                        dl_url = item.get("link") or item.get("directDownload")
                        name = item.get("name", "Untitled")
                        size = item.get("size", 0)
                        if dl_url:
                            found_videos.append({
                                "id": f"gofile:{item_id}",
                                "name": name,
                                "url": dl_url,
                                "size": f"{(size / (1024*1024)):.2f} MB"
                            })
        except Exception as e:
            print(f"Error reading folder {current_folder_id}: {e}")

    cached_videos = found_videos
    last_scan_time = time.time()
    return cached_videos

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
    videos = crawl_gofile()
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
    videos = crawl_gofile()
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
    videos = crawl_gofile()
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
