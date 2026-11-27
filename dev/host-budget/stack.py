"""Disposable 2 GiB guest ONLY. Each Docker process is charged to this guest."""
import base64
import concurrent.futures
import gzip
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path.cwd()
OUT = Path("/var/tmp/host-budget")
OUT.mkdir()
full = sys.argv[1] == "default"
app = "ghcr.io/sam-dumont/wow-mcp:0.13.0@sha256:03e3d9297da20d82d9dfedb6f6a6fab361760842eed9fead52ad7902678b5aa3"
render = "ghcr.io/sam-dumont/wow-mcp-render:0.13.0@sha256:2f26dc2da261cefc368e07e3c1f9ad20844a8bf5f01f933455ff3afffcc23a4e"
images = re.findall(r"^    image: (docker.io/victoriametrics/\S+)$", (ROOT/"deploy/compose/compose.yaml").read_text(), re.M)
vl, vm = images
driver = "/review/dev/host-budget/driver.py"
def log(msg, **fields):
    print(json.dumps({"step":msg, **fields}), flush=True)
def cmd(args, timeout=120):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout).stdout.strip()
def docker(*args, timeout=120):
    try:
        return cmd(["docker", *args], timeout)
    except subprocess.CalledProcessError as e:
        raise RuntimeError("docker " + str(args[:2]) + ": " + e.stderr[-600:]) from None
def req(url, body=None, headers=None, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers or {}), timeout=timeout) as resp:
        return resp.read()
def wait(fn, timeout=90):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if fn():
                return
        except OSError:
            pass
        time.sleep(.1)
    raise RuntimeError("readiness timeout")
def start(name, image, flags, args):
    docker("run", "-d", "--pull=never", "--name",name, "--network","host",
           "--cap-drop","ALL","--security-opt","no-new-privileges:true","--read-only","--tmpfs","/tmp",
           *flags, image, *args)
def await_exit(name, timeout=1050):
    rc = docker("wait",name,timeout=timeout)
    lines = docker("logs",name).splitlines()
    for line in lines[-14:]:
        print(name, line, flush=True)
    if rc != "0":
        # Application logs may include secrets in OAuth registration output. Only a trailing traceback for this job.
        tail = subprocess.run(["docker","logs","--tail","8",name], capture_output=True, text=True).stderr
        print(name,tail, flush=True)
        raise RuntimeError(name + " exit " + rc)
def app_job(name, args, image=app, flags=(), **extra):
    service_env = {**env, **extra}
    args_env = [k for kv in service_env.items() for k in ("-e","=".join(kv))]
    start(name,image,["-v",f"{ROOT}:/review:ro","-v",f"{OUT}:/bench","--entrypoint","python",*flags,*args_env],args)
def driver_job(name,*args,**extra):
    app_job(name,[driver,*args],**extra)
def seed(name):
    auth = base64.b64encode(("wow:" + env["INGEST_PASSWORD"]).encode()).decode()
    hp = {"Content-Type":"application/x-ndjson","Authorization":"Basic "+auth}
    path = OUT / "initial.gz" if name == "initial" else OUT / "archive/sent" / (name + ".gz")
    with gzip.open(path, "rb") as f:
        buf=[]
        for line in f:
            buf.append(line)
            if len(buf)==2000:
                reply=json.loads(req("http://127.0.0.1:8080/ingest",b"".join(buf),hp))
                assert not reply.get("rejected"),reply
                buf=[]
        if buf:
            reply=json.loads(req("http://127.0.0.1:8080/ingest",b"".join(buf),hp))
            assert not reply.get("rejected"),reply
def timed(name, fn):
    t=time.monotonic()
    result=fn()
    log(name, seconds=round(time.monotonic()-t,3))
    return result
def start_vl(name,path):
    start(name,vl,["--user","10001:10001","--memory","1g","-v",f"{path}:/data"],
          ["-storageDataPath=/data","-retentionPeriod=100y","-httpListenAddr=127.0.0.1:9428",
           "-search.maxQueryDuration=30s","-search.maxConcurrentRequests=4","-search.maxQueueDuration=10s"])
    wait_service(name, "http://127.0.0.1:9428/health")
def wait_service(name, url):
    def healthy():
        if docker("inspect", "-f", "{{.State.Running}}", name) != "true":
            res=subprocess.run(["docker", "logs", "--tail", "5", name], capture_output=True, text=True)
            raise RuntimeError(name+" exited: "+(res.stdout+res.stderr)[-1500:])
        req(url)
        return True
    wait(healthy)

def start_ui(name,path):
    app_job(name,["-m","wowstats","ui"],flags=["--memory","1g"], **({} if full else {"EVENTS_STORE":"sqlite:/bench/"+str(path.relative_to(OUT))}))
    wait_service(name,"http://127.0.0.1:8080/healthz")
def counts(path):
    expected=json.loads((OUT/"expected.json").read_text())
    result={}
    if full:
        req("http://127.0.0.1:9428/internal/force_flush",b"")
        for char in expected:
            body=urllib.parse.urlencode({"query":f'char:"{char}" | stats count_uniq(source_id) as n'}).encode()
            result[char]=int(json.loads(req("http://127.0.0.1:9428/select/logsql/query",body))["n"])
    else:
        for db in path.glob("*.sqlite"):
            with sqlite3.connect(f"file:{db}?mode=ro",uri=True) as cx:
                assert cx.execute("pragma integrity_check").fetchone()==("ok",)
                for char, n, distinct in cx.execute("select char,count(*),count(distinct source_id) from events where char is not null group by char"):
                    if char in expected:
                        assert n==distinct,(char,n,distinct)
                        result[char]=n
    assert result==expected,(result,expected)
    log("verified event counts",distinct_ids=result)
def sample(stop):
    out=OUT/"samples.csv"
    with out.open("w",1) as f:
        f.write("epoch,total_kib,available_kib,free_disk_bytes,oom_kills\n")
        while True:
            info={s.split()[0].rstrip(":"):int(s.split()[1]) for s in Path("/proc/meminfo").read_text().splitlines()}
            kills=re.search(r"^oom_kill (\d+)$",Path("/proc/vmstat").read_text(),re.M)[1]
            f.write(f"{time.time():.3f},{info['MemTotal']},{info['MemAvailable']},{shutil.disk_usage(OUT).free},{kills}\n")
            if stop.wait(.25):
                break
def last_good():
    return {p:hashlib.sha256(req("http://127.0.0.1:8084/"+p)).hexdigest() for p in ["","data/chars.json","zogzog/"]}
def restore(location):
    archive=OUT/"events.tgz"
    timed("backup stopped events",lambda:cmd(["tar","czf",str(archive),"-C",str(location),"."]))
    target=OUT/"restored"
    target.mkdir()
    timed("extract to new directory",lambda:cmd(["tar","xzf",str(archive),"-C",str(target)]))
    def manifest(dir):
        return {p.relative_to(dir):hashlib.file_digest(p.open("rb"),"sha256").hexdigest() for p in dir.rglob("*") if p.is_file()}
    assert manifest(location)==manifest(target)
    log("backup matched",archive_bytes=archive.stat().st_size,allocated=cmd(["du","-s","-B1",str(location)]).split()[0])
    return target

for folder in ["state","events","site","cache","vm","vl","render"]:
    (OUT/folder).mkdir()
cmd(["chown","-R","10001:10001",str(OUT)])
secretfile = lambda n: "/bench/state/"+n
for file,value in [("oidc","standin-secret-0123456789abcdef"),("sign",secrets.token_urlsafe(48)),("render",secrets.token_urlsafe(32))]:
    (OUT/"state"/file).write_text(value)
env={"PYTHONPATH":"/review","PYTHONUNBUFFERED":"1","VIEW":"full","AUTH":"open","BIND":"127.0.0.1","PORT":"8080",
     "INGEST_PASSWORD":secrets.token_urlsafe(20),"OIDC_ALLOWED_SUBS":"owner","MCP_PUBLIC_URL":"http://127.0.0.1:8090/mcp",
     "OIDC_ISSUER":"http://127.0.0.1:8441","OIDC_CLIENT_ID":"wow-app","OIDC_CLIENT_SECRET_FILE":secretfile("oidc"),
     "MCP_SIGNING_KEY_FILE":secretfile("sign"),"ANALYSIS_STATE_DIR":secretfile("analysis"),"MCP_STATE_DIR":secretfile("mcp"),
     "MCP_PORT":"8090","ANALYSIS_SHARE_TOKEN_FILE":secretfile("share"),"ANALYSIS_SHARE_PORT":"8082",
     "ANALYSIS_SHARE_URL":"http://127.0.0.1:8082/analysis.json","SITE_DIR":"/bench/site","PUBLISH_DIR":"/bench/site",
     "ZOGZOG_KEY_FILE":secretfile("site.key"),"MODEL3D_CACHE_DIR":"/bench/cache","MODEL3D_ASSETS_DIR":"/bench/models",
     "MODEL3D":"on" if full else "off","WOW_ITEMS":"on" if full else "off","VL_URL":"http://127.0.0.1:9428",
     "DEFAULT_CHAR":"Nibbet-Gearholt","FASTMCP_CHECK_FOR_UPDATES":"off"}
if full:
    env.update(VM_URL="http://127.0.0.1:8428",RENDER_URL="http://127.0.0.1:8083",RENDER_TOKEN_FILE=secretfile("render"),
               RENDER_CACHE_DIR="/bench/render",RENDER_BUDGET_S="900")
else:
    env["EVENTS_STORE"]="sqlite:/bench/events"
for img in ([app,render,vl,vm] if full else [app]):
    docker("pull", img,timeout=480)
assert os.cpu_count()==2
assert int(Path("/proc/meminfo").read_text().splitlines()[0].split()[1]) in range(1750000,2097153)
assert len(Path("/proc/swaps").read_text().splitlines())==1
log("guest",profile=sys.argv[1],cpus=os.cpu_count(),disk_bytes=shutil.disk_usage(OUT).total)
driver_job("fixture","prepare")
await_exit("fixture",300)
driver_job("identity","oidc")
wait(lambda:req("http://127.0.0.1:8441/.well-known/openid-configuration"))
where=OUT/("vl" if full else "events")
if full:
    start_vl("events",where)
    start("metrics",vm,["--user","10001:10001","-v",f"{OUT}/vm:/data"],["-storageDataPath=/data","-httpListenAddr=127.0.0.1:8428","-retentionPeriod=23d"])
    wait_service("metrics", "http://127.0.0.1:8428/health")
    req(env["VM_URL"]+"/api/v1/import",(OUT/"metrics.ndjson").read_bytes())
    req(env["VM_URL"]+"/internal/force_flush")
    app_job("renderer",["-m","wowstats","render"],image=render,PORT="8083")
    wait_service("renderer","http://127.0.0.1:8083/healthz")
start_ui("ui",where)
app_job("static",["-m","wowstats","static"],PORT="8084")
stop=threading.Event()
thread=threading.Thread(target=sample,args=(stop,),daemon=True)
thread.start()
errors=[]
try:
    # Establish a valid previous site for a single character before adding the full history.
    seed("initial")
    if full:
        req(env["VL_URL"]+"/internal/force_flush",b"")
    driver_job("initial","publish",RENDER_URL="",MODEL3D="off",WOW_ITEMS="off")
    await_exit("initial")
    prior=last_good()
    link=os.readlink(OUT/"site/current")
    timed("upload full roster",lambda:seed("roster"))
    if full:
        req(env["VL_URL"]+"/internal/force_flush",b"")
    driver_job("interrupted","publish")
    wait(lambda:list((OUT/"site").glob(".build-*")),180)
    docker("kill","interrupted")
    assert os.readlink(OUT/"site/current")==link and last_good()==prior
    log("last good survived SIGKILL",current=link,sha256=prior)
    docker("restart","ui")
    wait(lambda:req("http://127.0.0.1:8080/healthz"))
    try:
        # Login first so the expensive first analysis, write and actual build overlap.
        driver_job("mcp-client","mcp")
        wait(lambda:(OUT/"logged-in").exists())
        stale=set((OUT/"site").glob(".build-*"))
        start_time=time.monotonic()
        driver_job("measured","publish")
        wait(lambda:set((OUT/"site").glob(".build-*"))-stale,180)
        (OUT/"go").touch()
        wait(lambda:(OUT/"mcp-start").exists())
        timed("in-flight POST",lambda:seed("overlap"))
        await_exit("mcp-client",240)
        await_exit("measured",1100)
        log("publish wall",seconds=round(time.monotonic()-start_time,3),target=os.readlink(OUT/"site/current"))
        driver_job("check","check-site")
        await_exit("check")
    except Exception as e:
        errors.append(str(e))
        log("concurrent workload failure",error=str(e))
    # The guest still needs to prove recovery even if the simultaneous work ran out of memory.
    for svc in ["measured","mcp-client","ui","renderer","metrics","identity","static"]:
        if svc in docker("ps","--format","{{.Names}}").splitlines():
            docker("stop","-t","8",svc)
    if full:
        docker("stop","events")
    restored=restore(where)
    if full:
        start_vl("restored",restored)
    counts(restored)
    if full:
        docker("stop","restored")
    fresh=OUT/"replay"
    fresh.mkdir()
    os.chown(fresh,10001,10001)
    if full:
        start_vl("replayed",fresh)
    docker("start","identity")
    wait(lambda:req("http://127.0.0.1:8441/.well-known/openid-configuration"))
    start_ui("replay-ui",fresh)
    config=OUT/"ship.toml"
    config.write_text("archive_dir = '/bench/archive'\nlog_file = '/tmp/ship.log'\nauto_update = false\n[ship]\n"
                      +"url = 'http://127.0.0.1:8080/ingest'\nuser = 'wow'\npassword = '"+env["INGEST_PASSWORD"]+"'\n")
    for i in (1,2):
        name="shipper-"+str(i)
        app_job(name,["/review/shipper/wow_stats.py","--replay"],WOW_STATS_CONFIG="/bench/ship.toml")
        timed("full shipper replay "+str(i),lambda:await_exit(name,300))
        counts(fresh)
finally:
    stop.set()
    thread.join()
    rows=[list(map(int,x.split(",")[1:])) for x in (OUT/"samples.csv").read_text().splitlines()[1:]]
    log("sampled host",samples=len(rows),memtotal_kib=rows[0][0],min_available_kib=min(x[1] for x in rows),
        peak_used_kib=max(x[0]-x[1] for x in rows),min_free_disk_bytes=min(x[2] for x in rows),
        start_disk_bytes=rows[0][2],oom_kills=rows[-1][3]-rows[0][3])
if errors:
    raise SystemExit("workload checks failed: "+", ".join(errors))
