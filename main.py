import os
import time
import threading
from collections import deque
from contextlib import asynccontextmanager
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
import requests

ROOT_FOLDER_ID = "OBVVp1LI"
cached_videos = []
last_scan_time = 0
crawl_lock = threading.Lock()

class GofileAuthManager:
    """Manages official Gofile guest session tokens via direct API."""
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Origin": "https://gofile.io",
            "Referer": "https://gofile.io/"
        })
        self.token = None
        self.last_auth_time = 0
        self.refresh_token()

    def refresh_token(self):
        print("🔑 Requesting fresh official Gofile API token...")
        try:
            # 1. Create a guest session
            res = self.session.post("https://api.gofile.io/accounts", timeout=15).json()
            if res.get("status") == "ok":
                self.token = res["data"]["token"]
                self.session.headers["Authorization"] = f"Bearer {self.token}"
                self.last_auth_time = time.time()
                print("✅ Guest authentication token generated successfully.")
                return
        except Exception as e:
            print(f"Token generation notice: {e}")

        # Fallback to public web headers if account creation rate-limits
        self.last_auth_time = time.time()

    def ensure_token(self):
        if not self.token or (time.time() - self.last_auth_time > 1800):
            self.refresh_token()

auth_mgr = GofileAuthManager()

def crawl_gofile_fast():
    global cached_videos, last_scan_time

    with crawl_lock:
        if cached_videos and (time.time() - last_scan_time < 1800):
            return cached_videos

        auth_mgr.ensure_token()
        folders_queue = deque([(ROOT_FOLDER_ID, "Root")])
        visited = set()
        all_files = {}

        print("🚀 Fast crawling Gofile directories...")

        while folders_queue:
            current_id, folder_name = folders_queue.popleft()
            if current_id in visited:
                continue
            visited.add(current_id)

            page = 1
            while True:
                api_url = f"https://api.gofile.io/contents/{current_id}?page={page}&pageSize=1000&sortField=createTime&sortDirection=-1"
                try:
                    res = auth_mgr.session.get(api_url, timeout=15).json()
                    if res.get("status") != "ok":
                        break

                    data = res.get("data", {})
                    children = data.get("children", {})
                    if not children:
                        break

                    for item_id, item in children.items():
                        if item.get("type") == "folder":
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
                                    "name": item.get("name", item_id),
                                    "url": dl_url,
                                    "size": size_mb
                                }

                    if page >= data.get("totalChildrenPages", 1) or len(children) == 0:
                        break
                    page += 1
                except Exception as err:
                    print(f"Directory crawl notice on {current_id}: {err}")
                    break

        if all_files:
            cached_videos = list(all_files.values())
            last_scan_time = time.time()
            print(f"✅ Fast crawl complete: {len(cached_videos)} playable files ready.")

        return cached_videos

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run scan immediately on startup in background
    threading.Thread(target=crawl_gofile_fast, daemon=True).start()
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
        "description": "Stream public Gofile files directly",
        "resources": ["catalog", "meta", "stream"],
        "types": ["other", "movie"],
        "catalogs": [
            {
                "type": "other",
                "id": "gofile_catalog",
                "name": "Gofile Library",
                "extra": [
                    {"name": "search", "isRequired": False},
                    {"name": "skip", "isRequired": False}
                ]
            }
        ]
    }

@app.get("/catalog/other/gofile_catalog.json")
@app.get("/catalog/other/gofile_catalog/{extra_params}.json")
def get_catalog(extra_params: str = None):
    videos = cached_videos or crawl_gofile_fast()
    
    search_query = None
    skip = 0

    if extra_params:
        for param in extra_params.split("&"):
            if param.startswith("search="):
                search_query = param.replace("search=", "").lower()
            elif param.startswith("skip="):
                try:
                    skip = int(param.replace("skip=", ""))
                except ValueError:
                    skip = 0

    filtered = [
        v for v in videos
        if not search_query or search_query in v["name"].lower()
    ]

    paged = filtered[skip:skip + 100] if skip else filtered

    metas = [
        {
            "id": vid["id"],
            "name": vid["name"],
            "type": "other",
            "poster": "https://gofile.io/dist/img/logo-small.png",
            "description": f"Size: {vid['size']}"
        }
        for vid in paged
    ]

    return {"metas": metas}

@app.get("/meta/other/{video_id}.json")
def get_meta(video_id: str):
    videos = cached_videos or crawl_gofile_fast()
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
    videos = cached_videos or crawl_gofile_fast()
    vid = next((v for v in videos if v["id"] == video_id), None)
    if not vid:
        raise HTTPException(status_code=404, detail="Stream not found")
    
    return {
        "streams": [
            {
                "title": f"Direct Stream: {vid['name']}",
                "url": vid["url"],
                "behaviorHints": {
                    "notWebReady": False
                }
            }
        ]
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
