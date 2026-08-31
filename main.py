import os
import time
import threading
from collections import deque
from contextlib import asynccontextmanager
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
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
                        captured["headers"] = dict(request.headers)

                page.on("request", intercept_request)
                try:
                    page.goto(self.root_url, wait_until="networkidle", timeout=45000)
                    time.sleep(3)
                except Exception as e:
                    print(f"Page load notice: {e}")

                browser.close()

            if captured["headers"]:
                self.session.headers.clear()
                self.session.headers.update(captured["headers"])
                self.last_auth_time = time.time()
                print("✅ Captured active Gofile session credentials.")
        except Exception as e:
            print(f"❌ Playwright launch error: {e}")

    def ensure_fresh(self):
        if time.time() - self.last_auth_time > 900 or not self.session.headers:
            self.refresh_credentials()

session_mgr = SessionManager(ROOT_URL)

def fetch_folder_with_backoff(session_mgr, folder_id, page_num=1, max_retries=4):
    api_url = f"https://api.gofile.io/contents/{folder_id}?page={page_num}&pageSize=1000&sortField=createTime&sortDirection=-1"
    
    for attempt in range(max_retries):
        session_mgr.ensure_fresh()
        try:
            res = session_mgr.session.get(api_url, timeout=20).json()
            status = res.get("status")

            if status == "ok":
                return res
            elif status in ["error-rateLimit", "error-auth", "error-token"]:
                cool_off = (attempt + 1) * 6
                print(f"⏳ [{status}] Backing off for {cool_off}s...")
                time.sleep(cool_off)
                if status in ["error-auth", "error-token"]:
                    session_mgr.refresh_credentials()
            else:
                return res
        except Exception:
            time.sleep(2)
            
    return None

def crawl_gofile_recursive():
    global cached_videos, last_scan_time

    with crawl_lock:
        if cached_videos and (time.time() - last_scan_time < 1800):
            return cached_videos

        session_mgr.ensure_fresh()
        folders_queue = deque([("OBVVp1LI", "Root Folder")])
        visited_folders = set()
        all_files = {}

        print("🚀 Crawling Gofile folder tree...")

        while folders_queue:
            current_folder_id, current_folder_name = folders_queue.popleft()
            if current_folder_id in visited_folders:
                continue
            visited_folders.add(current_folder_id)

            page_num = 1
            while True:
                res = fetch_folder_with_backoff(session_mgr, current_folder_id, page_num)
                if not res or res.get("status") != "ok":
                    break

                data = res.get("data", {})
                children = data.get("children", {})
                if not children:
                    break

                for item_id, item in children.items():
                    item_type = item.get("type", "")
                    item_name = item.get("name", item_id)

                    if item_type == "folder":
                        folder_code = item.get("code") or item.get("id") or item_id
                        if folder_code not in visited_folders and all(folder_code != f[0] for f in folders_queue):
                            folders_queue.append((folder_code, item_name))
                    else:
                        dl_url = item.get("link") or item.get("directDownload") or item.get("downloadPage")
                        if not dl_url:
                            dl_url = f"https://api.gofile.io/contents/{item_id}"

                        size = item.get("size", 0)
                        size_mb = f"{(size / (1024 * 1024)):.2f} MB" if size else "Unknown size"

                        if item_id not in all_files:
                            all_files[item_id] = {
                                "id": f"gofile:{item_id}",
                                "item_id": item_id,
                                "name": item_name,
                                "url": dl_url,
                                "size": size_mb
                            }

                total_pages = data.get("totalChildrenPages", 1)
                if page_num >= total_pages or len(children) == 0:
                    break
                page_num += 1

        total_list = list(all_files.values())
        if total_list:
            cached_videos = total_list
            last_scan_time = time.time()
            print(f"✅ Crawl complete: {len(cached_videos)} playable files found.")

        return cached_videos

@asynccontextmanager
async def lifespan(app: FastAPI):
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
    videos = cached_videos or crawl_gofile_recursive()
    
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

# Stream definition: route through the authenticated proxy
@app.get("/stream/other/{video_id}.json")
def get_stream(video_id: str, request: Request):
    videos = cached_videos or crawl_gofile_recursive()
    vid = next((v for v in videos if v["id"] == video_id), None)
    if not vid:
        raise HTTPException(status_code=404, detail="Stream not found")
    
    base_host = str(request.base_url).rstrip("/")
    clean_id = vid["item_id"]
    proxy_stream_url = f"{base_host}/proxy/stream/{clean_id}"

    return {
        "streams": [
            {
                "title": f"Play: {vid['name']}",
                "url": proxy_stream_url,
                "behaviorHints": {
                    "notWebReady": False
                }
            }
        ]
    }

# Authenticated Video Proxy with Full Byte-Range Seeking Support
@app.get("/proxy/stream/{item_id}")
def proxy_video_stream(item_id: str, request: Request):
    session_mgr.ensure_fresh()
    videos = cached_videos or crawl_gofile_recursive()
    vid = next((v for v in videos if v["item_id"] == item_id), None)
    
    if not vid or not vid.get("url"):
        raise HTTPException(status_code=404, detail="Video URL not found")

    target_url = vid["url"]
    
    # Forward Range headers for video seeking
    req_headers = dict(session_mgr.session.headers)
    if "range" in request.headers:
        req_headers["Range"] = request.headers["range"]

    try:
        upstream_res = requests.get(target_url, headers=req_headers, stream=True, timeout=25)
        
        # Strip hop-by-hop headers
        exclude_headers = {"content-encoding", "content-length", "transfer-encoding", "connection"}
        response_headers = {
            name: value for name, value in upstream_res.headers.items()
            if name.lower() not in exclude_headers
        }
        
        if "content-length" in upstream_res.headers:
            response_headers["Content-Length"] = upstream_res.headers["content-length"]
        if "content-range" in upstream_res.headers:
            response_headers["Content-Range"] = upstream_res.headers["content-range"]
        if "accept-ranges" in upstream_res.headers:
            response_headers["Accept-Ranges"] = upstream_res.headers["accept-ranges"]
        else:
            response_headers["Accept-Ranges"] = "bytes"

        def stream_generator():
            for chunk in upstream_res.iter_content(chunk_size=1024 * 128):
                if chunk:
                    yield chunk

        return StreamingResponse(
            stream_generator(),
            status_code=upstream_res.status_code,
            headers=response_headers,
            media_type=upstream_res.headers.get("content-type", "video/mp4")
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Proxy error: {str(e)}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
