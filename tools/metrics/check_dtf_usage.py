import json, subprocess, argparse, sys

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--workbench", required=True)
    wb = ap.parse_args().workbench
    subprocess.run(["git","-C",wb,"fetch","origin","main"], capture_output=True)
    def grep(p, path):
        return subprocess.run(["git","-C",wb,"grep","-n","-e",p,"origin/main","--",path],
                              capture_output=True, text=True, encoding='utf-8').stdout.strip()
    own = grep("CREATE TABLE IF NOT EXISTS", "6-msa") or grep("EnsureCreated", "6-msa")
    save = grep("SaveChangesAsync", "6-msa/**/*Command*.cs") or grep("SaveChangesAsync", "6-msa/**/Handlers*.cs")
    dtf = grep("DigiTFactory", "6-msa") or grep("EventStoreDbContext", "6-msa") or grep("AddEventStorePostgres", "6-msa")
    if own or save:
        print(json.dumps({"role":"BE","metric":"dtf_usage","status":"violation","value":"own infra","detail":f"CREATE_TABLE={bool(own)} SaveChanges_cmd={bool(save)}"}, ensure_ascii=False)); return 1
    if not dtf:
        print(json.dumps({"role":"BE","metric":"dtf_usage","status":"violation","value":"no DTF","detail":"no DigiTFactory refs"}, ensure_ascii=False)); return 1
    print(json.dumps({"role":"BE","metric":"dtf_usage","status":"ok","value":"DTF used","detail":""}, ensure_ascii=False)); return 0

if __name__ == "__main__":
    sys.exit(main())
