import json, os, hashlib, re, subprocess, argparse, sys

def body_hash(txt):
    body = re.sub(r"\*\*Статус:\*\*[^\n]*\n", "", txt)
    body = re.split(r"## 4\. Последствия", body)[0]
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--workbench", required=True)
    wb = ap.parse_args().workbench
    subprocess.run(["git","-C",wb,"fetch","origin","main"], capture_output=True)
    files = subprocess.run(["git","-C",wb,"ls-tree","-r","--name-only","origin/main","--","04-architecture/ADL/"],
                           capture_output=True, text=True, encoding="utf-8").stdout.splitlines()
    manifest = {}
    for f in files:
        if not f.startswith("04-architecture/ADL/adr-") or not f.endswith(".md"):
            continue
        txt = subprocess.run(["git","-C",wb,"show",f"origin/main:{f}"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
        if txt and re.search(r"\*\*Статус:\*\*\s*Принято", txt):
            manifest[os.path.basename(f)] = body_hash(txt)
    mp = os.path.join(wb, "04-architecture", "ADL", ".frozen.json")
    json.dump(manifest, open(mp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps({"role":"AR","metric":"adr_frozen","status":"ok","value":f"{len(manifest)} frozen","detail":"manifest created"}, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    sys.exit(main())
