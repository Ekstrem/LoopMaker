import json, subprocess, argparse, sys

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--workbench", required=True)
    wb = ap.parse_args().workbench
    subprocess.run(["git","-C",wb,"fetch","origin","main"], capture_output=True)
    files = subprocess.run(["git","-C",wb,"diff-tree","--no-commit-id","--name-only","-r","origin/main"],
                           capture_output=True, text=True, encoding='utf-8').stdout.splitlines()
    js = [f for f in files if f.startswith("8-front/js/")]
    if not js:
        print(json.dumps({"role":"FE","metric":"fe_version","status":"ok","value":"no js change","detail":""}, ensure_ascii=False)); return 0
    diff = subprocess.run(["git","-C",wb,"show","origin/main","--","8-front/js/app.js","8-front/sw.js"],
                          capture_output=True, text=True, encoding='utf-8').stdout
    ver, cache = "APP_VERSION" in diff, "CACHE" in diff
    st = "ok" if (ver and cache) else "violation"
    print(json.dumps({"role":"FE","metric":"fe_version","status":st,"value":f"APP_VERSION={ver} CACHE={cache}","detail":""}, ensure_ascii=False))
    return 0 if st == "ok" else 1

if __name__ == "__main__":
    sys.exit(main())
