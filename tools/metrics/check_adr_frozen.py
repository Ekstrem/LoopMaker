import json, os, hashlib, re, subprocess, argparse, sys

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--workbench", required=True)
    wb = ap.parse_args().workbench
    subprocess.run(["git","-C",wb,"fetch","origin","main"], capture_output=True)
    mp_txt = subprocess.run(["git","-C",wb,"show","origin/main:04-architecture/ADL/.frozen.json"],
                            capture_output=True, text=True).stdout
    if not mp_txt.strip():
        print(json.dumps({"role":"AR","metric":"adr_frozen","status":"unknown","value":"no manifest","detail":".frozen.json absent"}, ensure_ascii=False)); return 2
    manifest = json.loads(mp_txt)
    def body_hash(txt):
        body = re.sub(r"\*\*Статус:\*\*[^\n]*\n", "", txt)
        body = re.split(r"## 4\. Последствия", body)[0]
        return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    bad = []
    for name in manifest:
        txt = subprocess.run(["git","-C",wb,"show",f"origin/main:04-architecture/ADL/{name}"],
                             capture_output=True, text=True).stdout
        if txt and manifest[name] != body_hash(txt):
            bad.append(name)
    if bad:
        print(json.dumps({"role":"AR","metric":"adr_frozen","status":"violation","value":f"{len(bad)} mismatch","detail":",".join(bad)}, ensure_ascii=False)); return 1
    print(json.dumps({"role":"AR","metric":"adr_frozen","status":"ok","value":f"{len(manifest)} frozen","detail":""}, ensure_ascii=False)); return 0

if __name__ == "__main__":
    sys.exit(main())
