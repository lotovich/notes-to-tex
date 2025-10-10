
#!/usr/bin/env python3

# Batch runner for notes-to-tex 0.1.1-alpha.1

# - Sends every *.pdf/*.jpg/*.jpeg/*.png from a folder to POST /process
# - Saves the returned ZIP per job
# - Extracts LaTeX and computes quick metrics
# - Writes results/summary.csv

# Usage:
#   python run_folder_tests.py --input ./golden --out ./results \
#     --url http://localhost:8000/process --mode book --editor 1 --compile 0

import argparse
import csv
import io
import re
import time
from pathlib import Path
import zipfile
import requests
from requests.adapters import HTTPAdapter, Retry

def session_with_retries():
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[502, 503, 504])
    s.mount("http://", HTTPAdapter(max_retries=retries))
    s.mount("https://", HTTPAdapter(max_retries=retries))
    return s

def post_file(url: str, path: Path, mode: str, use_editor: bool, compile_pdf: bool):
    files = {"file": (path.name, open(path, "rb"), "application/octet-stream")}
    params = {
        "mode": mode,           # "book" (enriched) or "strict"
        "use_editor": str(use_editor).lower(),  # true/false accepted
        "compile_pdf": str(compile_pdf).lower()
    }
    s = session_with_retries()
    r = s.post(url, files=files, params=params, timeout=300)
    r.raise_for_status()
    return r.json()

def download_zip(url: str) -> bytes:
    s = session_with_retries()
    r = s.get(url, timeout=300)
    r.raise_for_status()
    return r.content

def safe_unzip(zip_bytes: bytes, dest: Path):
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        z.extractall(dest)

def read_tex(out_dir: Path) -> str:
    # try main.tex or any .tex
    main = out_dir / "main.tex"
    if main.exists():
        return main.read_text(errors="ignore")
    cand = list(out_dir.glob("*.tex"))
    if cand:
        return cand[0].read_text(errors="ignore")
    return ""

def count_equations(tex: str) -> int:
    return (len(re.findall(r"\\begin\{equation\*?\}", tex)) +
            len(re.findall(r"\\begin\{align\*?\}", tex)) +
            len(re.findall(r"\\\[\s*", tex)))

def count_figures(tex: str) -> int:
    return len(re.findall(r"\\includegraphics", tex))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="folder with pdf/jpg/jpeg/png")
    ap.add_argument("--out", default="./results", help="output folder")
    ap.add_argument("--url", default="http://localhost:8000/process", help="backend /process URL")
    ap.add_argument("--mode", default="book", choices=["book","strict"], help="LLM output mode")
    ap.add_argument("--editor", type=int, default=1, help="use editor pass (1/0)")
    ap.add_argument("--compile", type=int, default=0, help="backend compile PDF via latexmk (1/0)")
    args = ap.parse_args()

    in_dir = Path(args.input)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    files = [p for p in sorted(in_dir.iterdir()) if p.suffix.lower() in [".pdf",".jpg",".jpeg",".png"]]
    if not files:
        print("No test files found in", in_dir)
        return

    rows = []
    for i, f in enumerate(files, 1):
        case_dir = out_dir / f"{i:02d}_{f.stem}"
        case_dir.mkdir(parents=True, exist_ok=True)
        print(f"[{i}/{len(files)}] {f.name} → POST {args.url}")
        t0 = time.time()
        try:
            resp = post_file(args.url, f, args.mode, bool(args.editor), bool(args.compile))
            download_url = resp.get("download_url")
            stats = resp.get("stats", {})
            (case_dir / "response.json").write_text(str(resp), encoding="utf-8")

            tex = ""
            if download_url:
                zip_bytes = download_zip(download_url)
                (case_dir / "out.zip").write_bytes(zip_bytes)
                safe_unzip(zip_bytes, case_dir / "out")
                tex = read_tex(case_dir / "out")

            eqs = count_equations(tex)
            figs = count_figures(tex)
            elapsed = time.time() - t0
            print(f"   → ok in {elapsed:.1f}s | eq={eqs} fig={figs} (backend figures={stats.get('figures','?')})")

            rows.append({
                "file": f.name,
                "time_s": f"{elapsed:.1f}",
                "eq_detected": eqs,
                "fig_detected": figs,
                "backend_text_blocks": stats.get("text_blocks",""),
                "backend_figures": stats.get("figures",""),
                "mode": stats.get("mode",""),
                "editor_used": stats.get("editor_used",""),
                "pdf_built": stats.get("pdf_built",""),
                "result_dir": str(case_dir)
            })
        except Exception as e:
            rows.append({
                "file": f.name,
                "time_s": "",
                "eq_detected": "",
                "fig_detected": "",
                "backend_text_blocks": "",
                "backend_figures": "",
                "mode": args.mode,
                "editor_used": bool(args.editor),
                "pdf_built": "",
                "result_dir": str(case_dir),
                "error": str(e)
            })
            print(f"   ! ERROR: {e}")

    # CSV summary
    import csv
    csv_path = out_dir / "summary.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print("Summary →", csv_path)

if __name__ == "__main__":
    main()
