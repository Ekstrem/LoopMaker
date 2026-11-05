import json, os, glob, hashlib, re, argparse, sys

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--workbench", required=True)
    wb = ap.parse_args().workbench
    adl = os.path.join(wb, "04-architecture", "ADL")
    mp = os.path.join(adl, ".frozen.json")
    if not os.path.exists(mp):
        print(json.dumps({"role":"AR","metric":"adr_frozen","status":"unknown","value":"no manifest","detail":".frozen.json absent"}, ensure_ascii=False)); return 2
    manifest = json.load(open(mp, encoding="utf-8"))
    bad = []
    for f in sorted(glob.glob(os.path.join(adl, "adr-*.md"))):
        txt = open(f, encoding="utf-8").read()
        body = re.sub(r"\*\*Статус:\*\*[^\n]*\n", "", txt)
        body = re.split(r"## 4\. Последствия", body)[0]
        h = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
        name = os.path.basename(f)
        if name in manifest and manifest[name] != h:
            bad.append(name)
    if bad:
        print(json.dumps({"role":"AR","metric":"adr_frozen","status":"violation","value":f"{len(bad)} mismatch","detail":",".join(bad)}, ensure_ascii=False)); return 1
    print(json.dumps({"role":"AR","metric":"adr_frozen","status":"ok","value":f"{len(manifest)} frozen","detail":""}, ensure_ascii=False)); return 0

if __name__ == "__main__":
    sys.exit(main())
