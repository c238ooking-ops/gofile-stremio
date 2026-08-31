import os
import time
import threading
from collections import deque
from contextlib import asynccontextmanager
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from playwright.sync_api import sync_playwright
import requests

ROOT_URL = "https://gofile.io/d/OBVVp1LI"
cached_videos = []
last_scan_time = 0
crawl_lock = threading.Lock()

class SessionManager:
    def __init__(self, root_url):
        self.root_url = root_url
        self.session = requests.Session()
        self.last_auth_time = 0
        self.refresh_credentials()

    def refresh_credentials(self):
        print("🌐 Launching Playwright Chromium to intercept active session headers...")
        captured = {"headers": {}}
        try:
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
                    if "contents/" in request.url:
                        # Capture authorization headers sent by Gofile's official frontend
                        captured["headers"] = dict(request.headers)

                page.on("request", intercept_request)
                try:
                    page.goto(self.root_url, wait_until="networkidle", timeout=45000)
                    time.sleep(3)
                except Exception as e:
                    print(f"Page load note: {e}")
                
                browser.close()

            if captured["headers"]:
                self.session.headers.clear()
                self.session.headers.update(captured["headers"])
                self.last_auth_time = time.time()
                print("✅ Successfully captured active Gofile session credentials.")
            else:
                print("⚠️ Warning: No request to /contents/ was intercepted.")
        except Exception as e:
            print(f"❌ Playwright launch error: {e}")

    def ensure_fresh(self):
        if time.time() - self.last_auth_time > 900 or not self.session.headers:
            self.refresh_credentials()

def crawl_gofile_recursive():
    global cached_videos, last_scan_time

    with crawl_lock:
        if cached_videos and (time.time() - last_scan_time < 1800):
            return cached_videos

        session_mgr = SessionManager(ROOT_URL)
        session_mgr.ensure_fresh()

        folders_queue = deque([("OBVVp1LI", "Root Folder")])
        visited_folders = set()
        found_videos = []

        print("🚀 Crawling Gofile folder tree...")

        while folders_queue:
            current_folder_id, current_name = folders_queue.popleft()
            if current_folder_id in visited_folders:
                continue
            visited_folders.add(current_folder_id)

            page_num = 1
            while True:
                api_url = f"https://api.gofile.io/contents/{current_folder_id}?page={page_num}&pageSize=50&sortField=createTime&sortDirection=-1"
                try:
                    res = session_mgr.session.get(api_url, timeout=20).json()
                    status = res.get("status")

                    if status != "ok":
                        print(f"⚠️ Folder [{current_name}] response status: {status}")
                        break

                    data = res.get("data", {})
                    children = data.get("children", {})
                    if not children:
                        break

                    for item_id, item in children.items():
                        item_type = item.get("type", "")
                        item_name = item.get("name", "Untitled")

                        if item_type == "folder":
                            folder_code = item.get("code") or item.get("id") or item_id
                            if folder_code not in visited_folders:
                                folders_queue.append((folder_code, item_name))
                        else:
                            dl_url = item.get("link") or item.get("directDownload") or item.get("downloadPage")
                            size = item.get("size", 0)
                            size_mb = f"{(size / (1024 * 1024)):.2f} MB" if size else "Unknown size"

                            if dl_url:
                                found_videos.append({
                                    "id": f"gofile:{item_id}",
                                    "name": item_name,
                                    "url": dl_url,
                                    "size": size_mb
                                })

                    total_pages = data.get("totalChildrenPages", 1)
                    if page_num >= total_pages or len(children) == 0:
                        break
                    page_num += 1
                except Exception as e:
                    print(f"❌ Error querying {current_folder_id} page {page_num}: {e}")
                    break

        if found_videos:
            cached_videos = found_videos
            last_scan_time = time.time()
            print(f"🎉 Crawl finished: Found {len(cached_videos)} total playable files.")

        return cached_videos

# Lifespan event handler for FastAPI
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm up cache in background on startup
    threading.Thread(target=crawl_gofile_recursive, daemon=True).start()
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return RedirectResponse(url="/manifest.json")

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
    videos = cached_videos or crawl_gofile_recursive()
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
    videos = cached_videos or crawl_gofile_recursive()
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
    videos = cached_videos or crawl_gofile_recursive()
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
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
