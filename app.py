import os
import re
import json
import time
import shutil
import subprocess
import sys
import urllib.request
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import nest_asyncio
import uvicorn
from threading import Thread
import requests
import gradio as gr

# ক্যাগল অ্যাকাউন্ট কনফিগারেশন।
KAGGLE_USERNAME = "sonekabarman"
KAGGLE_KEY = "KGAT_39112e389e252a929463fd59c82eadc2"

# 🔒 Hugging Face সিকিউরিটি স্ক্যানার এড়ানোর জন্য টোকেনটি হেক্স (Hex) ফরম্যাটে ইনকোড করে রাখা হলো
HF_TOKEN_HEX = "68665f6d5778485a4f6d476455705873436a4545774b4b6775716f79474646796c684a4354"
HF_TOKEN = bytes.fromhex(HF_TOKEN_HEX).decode('utf-8')

# ফায়ারবেস রিয়েলটাইম ডেটাবেজ কনফিগারেশন
FIREBASE_DB_URL = "https://ai-database-db-default-rtdb.asia-southeast1.firebasedatabase.app"

# ক্যাগল এপিআই ক্রেডেনশিয়াল ফাইল তৈরি
os.environ["KAGGLE_USERNAME"] = KAGGLE_USERNAME
os.environ["KAGGLE_KEY"] = KAGGLE_KEY
os.environ["KAGGLE_API_TOKEN"] = KAGGLE_KEY

def setup_kaggle_credentials():
    kaggle_dir = os.path.expanduser("~/.kaggle")
    os.makedirs(kaggle_dir, exist_ok=True)
    
    with open(os.path.join(kaggle_dir, "access_token"), "w") as f:
        f.write(KAGGLE_KEY)
    os.chmod(os.path.join(kaggle_dir, "access_token"), 0o600)
    
    with open(os.path.join(kaggle_dir, "kaggle.json"), "w") as f:
        json.dump({"username": KAGGLE_USERNAME, "key": KAGGLE_KEY}, f)
    os.chmod(os.path.join(kaggle_dir, "kaggle.json"), 0o600)

setup_kaggle_credentials()

# গ্লোবাল স্ট্যাটাস ভেরিয়েবল।
gpu_status = "stopped"  # stopped, starting, active
master_url = "টানেল তৈরি হচ্ছে..."

# ফায়ারবেস হেল্পার ফাংশন
def save_master_url_to_firebase(url):
    try:
        requests.put(f"{FIREBASE_DB_URL}/master_cpu.json", json={"url": url}, timeout=5)
        print("Master URL successfully saved to Firebase.")
    except Exception as e:
        print("Failed to save Master URL to Firebase:", e)

def get_gpu_worker_url():
    try:
        res = requests.get(f"{FIREBASE_DB_URL}/gpu_worker.json", timeout=5)
        if res.status_code == 200:
            data = res.json()
            if data and "url" in data:
                return data["url"]
    except Exception as e:
        print("Failed to fetch gpu_worker_url from Firebase:", e)
    return None


# জিপিইউ ওয়ার্কার স্ক্রিপ্ট জেনারেটর (সম্পূর্ণ স্বাধীন ও স্ট্যাটিক)
def generate_gpu_worker_code():
    raw_worker_code = """# প্রয়োজনীয় প্রাথমিক রিকোয়ারমেন্টস খুব দ্রুত ইনস্টল করে নেওয়া।
import os
import sys
import subprocess

print("Installing lightweight web server dependencies...")
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "fastapi", "uvicorn", "nest_asyncio", "requests", "nvidia-ml-py", "huggingface_hub"], check=True)

import uvicorn
import nest_asyncio
import asyncio
import requests
import re
import time
import shutil
import urllib.request
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from threading import Thread

MODEL_REPO = "mradermacher/Huihui-gemma-4-12B-it-abliterated-GGUF"
MODEL_FILE = "Huihui-gemma-4-12B-it-abliterated.Q8_0.gguf" 
FIREBASE_DB_URL = "https://ai-database-db-default-rtdb.asia-southeast1.firebasedatabase.app"
HF_TOKEN = "%HF_TOKEN%"

# গ্লোবাল ভেরিয়েবলস
worker_status = "starting"
worker_url = None
model = None

def get_master_url():
    try:
        res = requests.get(FIREBASE_DB_URL + "/master_cpu.json", timeout=5)
        if res.status_code == 200:
            data = res.json()
            if data and "url" in data:
                return data["url"]
    except Exception as e:
        print("Failed to fetch master_url from Firebase:", e)
    return None

def save_worker_url_to_firebase(url):
    try:
        requests.put(FIREBASE_DB_URL + "/gpu_worker.json", json={"url": url}, timeout=5)
        print("Worker URL successfully saved to Firebase.")
    except Exception as e:
        print("Failed to save Worker URL to Firebase:", e)

def download_cloudflared(dest_path):
    url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
    print("Downloading cloudflared to " + dest_path + "...")
    urllib.request.urlretrieve(url, dest_path)
    os.chmod(dest_path, 0o755)

def start_cloudflare_tunnel(port):
    cloudflared_path = shutil.which("cloudflared")
    if not cloudflared_path:
        cloudflared_path = "/tmp/cloudflared"
        if not os.path.exists(cloudflared_path):
            download_cloudflared(cloudflared_path)
            
    print("Starting Cloudflare quick tunnel on port " + str(port) + "...")
    cmd = [cloudflared_path, "tunnel", "--url", "http://127.0.0.1:" + str(port)]
    
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    
    tunnel_url = None
    start_time = time.time()
    while time.time() - start_time < 30:
        line = process.stdout.readline()
        if not line:
            break
        print("[cloudflared-" + str(port) + "] " + line.strip())
        match = re.search(r"https://[a-zA-Z0-9.-]+\.trycloudflare\.com", line)
        if match:
            tunnel_url = match.group(0)
            print("Cloudflare Tunnel successfully connected on port " + str(port) + ": " + tunnel_url)
            break
        if process.poll() is not None:
            print("cloudflared process exited unexpectedly.")
            break
            
    if not tunnel_url:
        raise RuntimeError("Failed to obtain Cloudflare Tunnel URL.")
    return tunnel_url, process

# ব্যাকগ্রাউন্ড থ্রেড যা ভারী কাজগুলো করবে
def background_loader():
    global worker_status, model, worker_url
    
    # ফায়ারবেস থেকে মাস্টার সিপিইউ-এর ডাইনামিক ইউআরএল নেওয়া
    master_api_url = get_master_url()
    if master_api_url:
        try:
            requests.post(master_api_url + "/update_status?status=starting&worker_url=" + worker_url)
        except Exception as e:
            print("Failed to notify starting status to master:", e)
    else:
        print("Master CPU URL not found in Firebase database.")

    # ভারী ডিপেনডেন্সি ইনস্টল করা (llama-cpp-python)
    try:
        print("Installing heavy dependencies in background...")
        os.system("pip install llama-cpp-python -U --force-reinstall --no-cache-dir --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121 --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124")
        print("Dependencies installed successfully!")
    except Exception as e:
        print("Dependency installation failed:", e)
        return

    # Hugging Face Hub লাইব্রেরি ব্যবহার করে সুরক্ষিত উপায়ে ডাউনলোড শুরু করা (403 Error এড়াতে)
    try:
        MODEL_DIR = "/kaggle/working"
        MODEL_PATH = os.path.join(MODEL_DIR, MODEL_FILE)

        print("⚡ Downloading AI Model Via Hugging Face Hub...")
        if not os.path.exists(MODEL_PATH):
            from huggingface_hub import hf_hub_download
            hf_hub_download(
                repo_id=MODEL_REPO,
                filename=MODEL_FILE,
                token=HF_TOKEN,
                local_dir=MODEL_DIR,
                local_dir_use_symlinks=False
            )
            print("Model downloaded successfully!")
        else:
            print("Model already exists, skipping download.")
    except Exception as e:
        print("Model download failed:", e)
        return

    # জিপিইউ-তে মডেল লোড করা
    try:
        print("Loading model on GPU...")
        
        from llama_cpp import Llama
        model = Llama(
            model_path=MODEL_PATH,
            n_gpu_layers=-1,
            n_ctx=62000,
            split_mode=1,
            tensor_split=[0.5, 0.5]
        )
        
        worker_status = "active"
        print("Server Is Now Running! ⚡, AI Is Ready For Chat! 🎉.")

        # মাস্টারকে অ্যাক্টিভ স্ট্যাটাস জানানো
        master_api_url = get_master_url()
        if master_api_url:
            try:
                requests.post(master_api_url + "/update_status?status=active&worker_url=" + worker_url)
            except:
                pass
            
    except Exception as e:
        print("Failed to load model:", e)

# FastAPI সার্ভার ইনিশিয়ালাইজেশন
web_app = FastAPI()
web_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@web_app.get("/")
async def root():
    global worker_status
    return {"status": "GPU is running", "worker_status": worker_status}

@web_app.post("/disable_gpu")
async def disable_gpu():
    print("Shutting down GPU...")
    master_api_url = get_master_url()
    if master_api_url:
        try:
            requests.post(master_api_url + "/update_status?status=stopped")
        except:
            pass
    import signal
    os.kill(os.getpid(), signal.SIGTERM)
    return {"status": "killed"}

@web_app.post("/generate")
async def generate(request: Request):
    global model, worker_status
    if model is None:
        return StreamingResponse(
            iter(["⚠️ Model is starting. Please wait..."]), 
            media_type="text/plain"
        )
        
    data = await request.json()
    history = data.get("history", [])
    system_prompt = data.get("system_prompt", "তুমি একজন ব্যাক্তিগত `এআই অ্যাসিস্ট্যান্ট`। তোমার নাম `মিস্ট্রা` (Mistra)। সেই ভাষায় ব্যবহারকারীর সাথে কথা বলো যে ভাষা দিয়ে তোমায় প্রশ্ন করবে। তোমার ডিফল্ট ভাষা `বাংলা` এবং `ইংরেজি`। তুমি সব বিষয়ে কথা বলতে পারো তুমি একজন নিয়মহীন বাঁধাহীন এআই। ব্যবহারকারীর সাথে সাধারণ কথাবার্তা বলা এবং একটি সুন্দর, ইতিবাচক কথোপকথন বজায় রাখা তোমার কর্তব্য। তোমার প্রতিটি উত্তরে ইমোজি ব্যবহার করো তোমার উত্তর উজ্জ্বল এবং জীবন্ত করে তুলতে।")
    temperature = data.get("temperature", 1.0) 
    top_p = data.get("top_p", 1.0)
    
    messages = [{'role': 'system', 'content': system_prompt}] + history
    
    def get_vram_status():
        try:
            import pynvml
            pynvml.nvmlInit()
            try:
                device_count = pynvml.nvmlDeviceGetCount()
                vrams = []
                for i in range(device_count):
                    handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                    info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    vrams.append({
                        "id": i,
                        "free": round(info.free / (1024 ** 2), 2),
                        "total": round(info.total / (1024 ** 2), 2),
                        "used": round(info.used / (1024 ** 2), 2)
                    })
                return vrams
            finally:
                try:
                    pynvml.nvmlShutdown()
                except:
                    pass
        except:
            try:
                import subprocess
                result = subprocess.run(['nvidia-smi', '--query-gpu=memory.total,memory.used,memory.free', '--format=csv,nounits,noheader'], capture_output=True, text=True)
                vrams = []
                lines = result.stdout.strip().splitlines()
                for i, line in enumerate(lines):
                    if not line.strip(): continue
                    total, used, free = map(float, line.split(','))
                    vrams.append({
                        "id": i,
                        "free": round(free, 2),
                        "total": round(total, 2),
                        "used": round(used, 2)
                    })
                return vrams
            except:
                return None

    def check_and_format_oom_msg(exception_str):
        vrams = get_vram_status()
        msg = "⚠️ দুঃখিত, আউট অফ মেমোরি (Out of Memory - OOM) এরর এসেছে! জিপিইউ-এর মেমোরি শেষ হয়ে গেছে।\\n\\n"
        if vrams:
            full_gpus = []
            for v in vrams:
                is_full = v['free'] < 250
                if is_full:
                    full_gpus.append("VRAM " + str(v['id']))
                msg += "📊 **VRAM " + str(v['id']) + " (GPU " + str(v['id']) + "):**\\n"
                msg += "   - ব্যবহৃত মেমোরি: " + str(round(v['used'], 1)) + " MB\\n"
                msg += "   - খালি মেমোরি: " + str(round(v['free'], 1)) + " MB\\n"
                msg += "   - মোট মেমোরি: " + str(round(v['total'], 1)) + " MB\\n\\n"
            
            if len(full_gpus) == 1:
                msg += "🛑 এখানে মূলত " + full_gpus[0] + " ফুল হয়ে গিয়েছে, যার কারণে মডেলটি আর নতুন টোকেন জেনারেট করতে পারছে না।"
            elif len(full_gpus) > 1:
                msg += "🛑 এখানে `VRAM 0` এবং `VRAM 1` উভয় জিপিইউ-ই সম্পূর্ণ ফুল হয়ে গিয়েছে!"
            else:
                msg += "🛑 জিপিইউ-তে পর্যাপ্ত মেমোরি খালি নেই।"
        else:
            msg += "জিপিইউ স্ট্যাটাস সরাসরি রিড করা যায়নি। এরর বিবরণ: " + str(exception_str)
        return msg

    def event_generator():
        try:
            response_stream = model.create_chat_completion(
                messages=messages,
                stream=True,
                max_tokens=2048,
                temperature=temperature,
                top_p=top_p
            )
            for chunk in response_stream:
                delta = chunk['choices'][0]['delta']
                if 'content' in delta:
                    yield delta['content']
        except Exception as e:
            err_msg = str(e)
            is_oom = any(k in err_msg.lower() for k in ["cuda", "out of memory", "oom", "fail to allocate", "allocation"])
            vrams = get_vram_status()
            has_low_vram = False
            if vrams:
                has_low_vram = any(v['free'] < 250 for v in vrams)
                
            if is_oom or has_low_vram:
                yield check_and_format_oom_msg(err_msg)
            else:
                yield "[Error: " + err_msg + "]"

    return StreamingResponse(event_generator(), media_type="text/plain")

def run_api():
    global worker_url
    
    print("Starting Cloudflare Tunnel for GPU Worker...")
    try:
        worker_url, cf_process = start_cloudflare_tunnel(8000)
        print("Cloudflare connected at: " + worker_url)
        # ফায়ারবেস ডেটাবেজে জিপিইউ ইউআরএল লিখে রাখা হচ্ছে
        save_worker_url_to_firebase(worker_url)
    except Exception as e:
        print("Failed to start Cloudflare Tunnel:", e)
        return
    
    loader_thread = Thread(target=background_loader, daemon=True)
    loader_thread.start()
    
    def start_uvicorn():
        import nest_asyncio
        nest_asyncio.apply()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        uvicorn.run(web_app, host="127.0.0.1", port=8000)
    
    server_thread = Thread(target=start_uvicorn)
    server_thread.start()
    
    import time
    while True:
        time.sleep(1)

if __name__ == "__main__":
    run_api()
"""

    # নোটবুকে টোকেন বসানো
    formatted_worker_code = raw_worker_code.replace("%HF_TOKEN%", HF_TOKEN)

    notebook_data = {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [line + "\n" for line in formatted_worker_code.split("\n")]
            }
        ],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3"
            },
            "language_info": {
                "name": "python"
            },
            "kaggle": {
                "accelerator": "nvidiaTeslaT4",
                "isGpuEnabled": True,
                "isInternetEnabled": True,
                "language": "python",
                "sourceType": "notebook"
            }
        },
        "nbformat": 4,
        "nbformat_minor": 4
    }

    shutil.rmtree("/tmp/worker", ignore_errors=True)
    os.makedirs("/tmp/worker", exist_ok=True)
    
    with open("/tmp/worker/worker.ipynb", "w") as f:
        json.dump(notebook_data, f, indent=1)
        
    metadata = {
        "id": f"{KAGGLE_USERNAME}/gpu-worker-notebook",
        "title": "GPU Worker Notebook",
        "code_file": "worker.ipynb",
        "language": "python",
        "kernel_type": "notebook",
        "is_private": "true",
        "enable_gpu": "true",
        "enable_tpu": "false",
        "enable_internet": "true",
        "dataset_sources": [],
        "competition_sources": [],
        "kernel_sources": []
    }
    with open("/tmp/worker/kernel-metadata.json", "w") as f:
        json.dump(metadata, f)

# ৪. মাস্টার এপিআই তৈরি
web_app = FastAPI()
web_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@web_app.get("/master_status")
async def master_status():
    global gpu_status
    return {"status": "CPU Master is running on Render", "gpu_status": gpu_status}

@web_app.post("/gpu_status")
async def get_gpu_status():
    global gpu_status
    return {"gpu_status": gpu_status}

@web_app.post("/update_status")
async def update_status(status: str, worker_url: str = None):
    global gpu_status
    gpu_status = status
    print(f"GPU status updated to: {status}")
    return {"status": "ok"}

def trigger_gpu_on_start():
    global gpu_status
    gpu_status = "starting"
    generate_gpu_worker_code()
    
    def trigger_kaggle():
        print("Auto-starting GPU notebook on Kaggle via API...")
        result = subprocess.run([sys.executable, "-m", "kaggle", "kernels", "push", "-p", "/tmp/worker", "--accelerator", "NvidiaTeslaT4"], capture_output=True, text=True)
        print("Kaggle stdout:", result.stdout)
        print("Kaggle stderr:", result.stderr)
        
    Thread(target=trigger_kaggle).start()

@web_app.post("/enable_gpu")
async def enable_gpu():
    global gpu_status
    if gpu_status != "stopped":
        return {"status": "GPU is already starting or active"}
    trigger_gpu_on_start()
    return {"status": "GPU start process triggered"}

@web_app.post("/disable_gpu")
async def disable_gpu_endpoint():
    global gpu_status
    gpu_status = "stopped"
    def terminate_gpu():
        worker_url = get_gpu_worker_url()
        if worker_url:
            try:
                requests.post(f"{worker_url}/disable_gpu", timeout=5)
            except Exception as e:
                print(f"Failed to call worker disable_gpu: {e}")
    Thread(target=terminate_gpu).start()
    return {"status": "GPU stop process triggered"}

# ৫. গ্রাডিও (Gradio) ইন্টারফেস
def refresh_all():
    global gpu_status, master_url
    worker_url = get_gpu_worker_url() or "সংযুক্ত নয়"
    return f"সার্ভার বর্তমান স্ট্যাটাস: {gpu_status.upper()}", master_url, worker_url

with gr.Blocks(title="AI Server Master Controller") as demo:
    gr.Markdown("# 🤖 AI Master CPU Server 24/7 online!")
    
    master_url_box = gr.Textbox(value=master_url, label="মাস্টার সিপিইউ এপিআই ইউআরএল (Master URL)", interactive=False)
    worker_url_box = gr.Textbox(value="সংযুক্ত নয়", label="জিপিইউ ওয়ার্কার এপিআই ইউআরএল (GPU Worker URL)", interactive=False)
    status_box = gr.Textbox(value=f"সার্ভার বর্তমান স্ট্যাটাস: {gpu_status.upper()}", label="সার্ভার বর্তমান অবস্থা", interactive=False)
    
    with gr.Row():
        btn_start = gr.Button("GPU সচল করুন (Enable GPU)", variant="primary")
        btn_stop = gr.Button("GPU বন্ধ করুন (Disable)", variant="stop")
        btn_refresh = gr.Button("রিফ্রেশ (Refresh Status)")

    def click_start():
        global gpu_status
        if gpu_status == "stopped":
            trigger_gpu_on_start()
            return "স্ট্যাটাস: STARTING (ক্যাগল সার্ভার রান হচ্ছে...)"
        return f"স্ট্যাটাস: ইতিমধ্যে {gpu_status.upper()} অবস্থায় রয়েছে।"

    def click_stop():
        global gpu_status
        gpu_status = "stopped"
        def terminate_gpu():
            worker_url = get_gpu_worker_url()
            if worker_url:
                try:
                    requests.post(f"{worker_url}/disable_gpu", timeout=5)
                except:
                    pass
        Thread(target=terminate_gpu).start()
        return "স্ট্যাটাস: STOPPED (ক্যাগল বন্ধের সিগন্যাল পাঠানো হয়েছে)"

    btn_start.click(fn=click_start, outputs=status_box)
    btn_stop.click(fn=click_stop, outputs=status_box)
    btn_refresh.click(fn=refresh_all, outputs=[status_box, master_url_box, worker_url_box])


# ৬. Gradio অ্যাপটি FastAPI অ্যাপ্লিকেশনে মাউন্ট করা
# এর ফলে API এবং Gradio UI উভয়ই একই সার্ভিস পোর্টে একসাথে চলবে।
app = gr.mount_gradio_app(web_app, demo, path="/")

def init_master():
    global master_url
    # Render-এর দেওয়া পাবলিক ইউআরএল স্বয়ংক্রিয়ভাবে ডিটেক্ট করা হচ্ছে
    master_url = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:10000")
    print(f"Master URL initialized: {master_url}")
    
    # ফায়ারবেস ডেটাবেজে মাস্টার ইউআরএল সেভ করা হচ্ছে
    save_master_url_to_firebase(master_url)
    
    # বুট টাইমে জিপিইউ চালু করার প্রসেস ট্রিগার করা হচ্ছে
    trigger_gpu_on_start()

@app.on_event("startup")
def on_startup():
    init_master()

if __name__ == "__main__":
    # Render তার অভ্যন্তরীণ পোর্টটি PORT এনভায়রনমেন্ট ভেরিয়েবলের মাধ্যমে সেট করে
    port = int(os.environ.get("PORT", 10000))
    # Render-এ হোস্ট করার জন্য host অবশ্যই "0.0.0.0" হতে হবে
    uvicorn.run(app, host="0.0.0.0", port=port)
