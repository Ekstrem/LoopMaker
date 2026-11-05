import json, os, glob, hashlib, re, argparse, sys

def body_hash(txt):
    body = re.sub(r"\*\*Статус:\*\*[^\n]*\n", "", txt)
    body = re.split(r"## 4\. Последствия", body)[0]
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--workbench", required=True)
    wb = ap.parse_args().workbench
    adl = os.path.join(wb, "04-architecture", "ADL")
    manifest = {}
    for f in sorted(glob.glob(os.path.join(adl, "adr-*.md"))):
        txt = open(f, encoding="utf-8").read()
        if re.search(r"\*\*Статус:\*\*\s*Принято", txt):
            manifest[os.path.basename(f)] = body_hash(txt)
    mp = os.path.join(adl, ".frozen.json")
    json.dump(manifest, open(mp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps({"role":"AR","metric":"adr_frozen","status":"ok","value":f"{len(manifest)} frozen","detail":"manifest created"}, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    sys.exit(main())
