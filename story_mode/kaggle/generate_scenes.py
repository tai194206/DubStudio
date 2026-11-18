"""Vẽ ảnh người que 2D cho storyboard.json trên Kaggle GPU T4.

Dùng: Add Input -> upload storyboard.json, Accelerator = GPU T4, Run.
Đầu ra: /kaggle/working/scenes/NNN.png, /kaggle/working/scenes/manifest.json, /kaggle/working/scenes.zip

Chuyển động (zoom/pan) KHÔNG làm ở đây: nó được áp ở bước dựng phim (story/story.py) từ trường `motion`.
"""
import gc
import glob
import json
import time
import zipfile
from pathlib import Path

import torch
from diffusers import AutoencoderKL, EulerDiscreteScheduler, StableDiffusionXLPipeline

# 0. Dọn VRAM nếu lần chạy trước bị crash
if "pipe" in globals():
    del pipe
gc.collect()
torch.cuda.empty_cache()

# 1. Tìm storyboard.json
found = glob.glob("/kaggle/input/**/storyboard.json", recursive=True) + glob.glob("./storyboard.json")
assert found, "Chưa thấy storyboard.json. Hãy Add Input -> Upload file storyboard.json."
STORY_PATH = found[0]
print("📄 Đang dùng:", STORY_PATH)
story = json.loads(Path(STORY_PATH).read_text(encoding="utf-8-sig"))

# Style đặt ĐẦU prompt: CLIP chỉ đọc 77 token đầu, nên phần quan trọng nhất phải nằm trước
STYLE_PREFIX = (
    "2D flat vector cartoon, OverSimplified style, cute round white stick-figure, "
    "bold black outlines, solid flat colors, minimalist background"
)
NEGATIVE_PROMPT = (
    "photorealistic, 3d render, realistic human, anime, messy lines, blurry, low quality, "
    "text, letters, watermark, signature, logo, extra limbs, deformed, cropped"
)
WIDTH, HEIGHT = 768, 1344          # tỉ lệ 9:16, bội số của 64 (SDXL yêu cầu)
BASE_SEED = 42
STEPS = 25
GUIDANCE = 7.5

OUT_DIR = Path("/kaggle/working/scenes")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load_with_dtype(loader, *args, **kwargs):
    """diffusers đổi tên tham số `torch_dtype` -> `dtype` ở bản mới; thử tên mới trước."""
    try:
        return loader(*args, dtype=torch.float16, **kwargs)
    except TypeError:
        return loader(*args, torch_dtype=torch.float16, **kwargs)


# 2. Load VAE fp16-fix + SDXL Base
print("🚀 Đang load VAE fp16-fix và SDXL...")
vae = load_with_dtype(AutoencoderKL.from_pretrained, "madebyollin/sdxl-vae-fp16-fix")
pipe = load_with_dtype(
    StableDiffusionXLPipeline.from_pretrained,
    "stabilityai/stable-diffusion-xl-base-1.0",
    vae=vae, variant="fp16", use_safetensors=True,
)
pipe.scheduler = EulerDiscreteScheduler.from_config(pipe.scheduler.config)
pipe.enable_model_cpu_offload()   # luân chuyển từng phần lên GPU, tránh OOM trên T4 16GB
pipe.vae.enable_tiling()          # giải mã ảnh theo ô nhỏ để tiết kiệm VRAM


def token_count(text: str) -> int:
    return len(pipe.tokenizer(text).input_ids)


# 3. Vẽ từng cảnh (bỏ qua ảnh đã có để chạy tiếp được sau khi bị ngắt)
scenes = story["scenes"]
manifest = {"title": story.get("title", ""), "width": WIDTH, "height": HEIGHT, "scenes": [], "failed": []}
t_all = time.time()

for sc in scenes:
    sid = int(sc["id"])
    out_img = OUT_DIR / f"{sid:03d}.png"
    if out_img.exists():
        print(f"⏭️ Bỏ qua cảnh {sid:03d} (đã có)")
        manifest["scenes"].append({"id": sid, "file": out_img.name, "status": "cached"})
        continue

    prompt = f"{STYLE_PREFIX}, {sc['image_prompt'].strip()}"
    n_tok = token_count(prompt)
    if n_tok > 77:
        print(f"⚠️ Cảnh {sid:03d}: prompt {n_tok} token > 77, phần cuối sẽ bị cắt. Nên rút gọn image_prompt.")

    t0 = time.time()
    gen = torch.Generator(device="cpu").manual_seed(BASE_SEED + sid)
    try:
        with torch.inference_mode():
            img = pipe(
                prompt=prompt,
                negative_prompt=NEGATIVE_PROMPT,
                width=WIDTH,
                height=HEIGHT,
                num_inference_steps=STEPS,
                guidance_scale=GUIDANCE,
                generator=gen,
            ).images[0]
        img.save(out_img)
        manifest["scenes"].append({"id": sid, "file": out_img.name, "status": "ok", "prompt_tokens": n_tok})
        print(f"✅ Cảnh {sid:03d}/{len(scenes)} ({time.time() - t0:.1f}s) -> {out_img.name}")
    except torch.cuda.OutOfMemoryError:
        gc.collect()
        manifest["failed"].append({"id": sid, "reason": "CUDA out of memory"})
        print(f"❌ Cảnh {sid:03d}: hết VRAM, bỏ qua. Chạy lại notebook để vẽ tiếp phần còn thiếu.")
    finally:
        torch.cuda.empty_cache()

# 4. Ghi manifest và nén
(OUT_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
zip_path = Path("/kaggle/working/scenes.zip")
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(OUT_DIR.glob("*.png")):
        z.write(p, p.name)
    z.write(OUT_DIR / "manifest.json", "manifest.json")

done = sum(1 for s in manifest["scenes"] if s["status"] in ("ok", "cached"))
print(f"🎉 Xong {done}/{len(scenes)} cảnh trong {(time.time() - t_all) / 60:.1f} phút -> {zip_path}")
if manifest["failed"]:
    print("⚠️ Chưa vẽ được:", [f["id"] for f in manifest["failed"]])
