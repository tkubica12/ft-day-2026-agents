"""Clean start and season control for the Otto budget-season demo.

`inspect` reads state, `backup` copies it locally, `reset` clears the previous scenario from the runtime and gateway
(it requires a fresh backup receipt), `season` runs the budget-planning admin CLI inside the private MCP sandbox.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from azure.containerapps.sandbox import SandboxGroupClient
from azure.identity import AzureCliCredential

from scripts.sandbox_runtime import endpoint_for_region
from scripts.sandbox_services import (
    DISK_IMAGE_AUDIENCE, SERVICE_CONFIG_PATH, require_exec_success, require_quiescent_service,
    require_service_health, service_maintenance,
)
from scripts.setup_agent365 import load_json
from scripts.setup_app_tfvars import runtime_outputs_path
from scripts.tf_helpers import REPO_ROOT


ROOT = REPO_ROOT / ".local" / "otto" / "budget-season"
PLAYBOOK = REPO_ROOT / "assignments" / "budget-season" / "budget-season-fy2027" / "SKILL.md"
PLAYBOOK_REMOTE = "/data/hermes/skills/private/budget-season-fy2027/SKILL.md"
BACKUP_MAX_AGE = timedelta(hours=6)
ROLES = {
    "runtime": ("runtime", "runtime_sandbox_id", 8642),
    "gateway": ("gateway", "bridge_sandbox_id", 8000),
    "private-mcp": ("private-mcp", "private_mcp_sandbox_id", 8765),
}
ADMIN = ("-m", "budget_planning_mcp.admin")

RUNTIME_INSPECT = r"""
import json,pathlib,sqlite3,subprocess,urllib.request
c=json.load(open('/app/.sandbox-service.json'));e=c['environment']
home=pathlib.Path('/data/hermes')
def get(p):
 q=urllib.request.Request('http://127.0.0.1:8642'+p,headers={'X-Autopilot-Key':e['API_SERVER_KEY']})
 with urllib.request.urlopen(q,timeout=20) as r:return json.load(r)
def size(p):
 return sum(f.stat().st_size for f in p.rglob('*') if f.is_file() and not f.is_symlink()) if p.is_dir() else (p.stat().st_size if p.exists() else 0)
def children(p):
 return sorted(x.name for x in p.iterdir()) if p.is_dir() else []
db={}
s=home/'state.db'
if s.exists():
 d=sqlite3.connect('file:'+str(s)+'?mode=ro',uri=True)
 tables=[r[0] for r in d.execute("select name from sqlite_master where type='table'")]
 db={'tables':{t:d.execute('select count(*) from "'+t+'"').fetchone()[0] for t in tables if not t.startswith('sqlite_')}}
 d.close()
ledger=home/'skills/.curator_ledger.jsonl'
print(json.dumps({
 'release':c.get('release'),'profile':e.get('WORKER_ASSIGNMENT_PROFILE',''),
 'status':get('/internal/runtime/status'),'cron':len(get('/internal/cron/jobs').get('jobs',[])),
 'top':{x.name:size(x) for x in sorted(home.iterdir())},
 'privateSkills':children(home/'skills/private'),'candidates':children(home/'skills/candidates'),
 'workspace':children(home/'workspace'),
 'memories':{x.name:x.stat().st_size for x in sorted((home/'memories').iterdir()) if x.is_file()} if (home/'memories').is_dir() else {},
 'ledgerLines':len(ledger.read_text().splitlines()) if ledger.exists() else None,
 'playbook':(home/'skills/private/budget-season-fy2027/SKILL.md').exists(),
 'stateDb':db,
}))
"""

GATEWAY_INSPECT = r"""
import json,sqlite3,urllib.request
c=json.load(open('/app/.sandbox-service.json'));e=c['environment']
q=urllib.request.Request('http://127.0.0.1:8000/internal/transport/status',headers={'X-Autopilot-Key':e['API_SERVER_KEY']})
with urllib.request.urlopen(q,timeout=20) as r:status=json.load(r)
d=sqlite3.connect('file:'+e['TRANSPORT_JOURNAL_PATH']+'?mode=ro',uri=True)
keys={}
for (k,) in d.execute('select key from conversation_state'):
 p=k.split(':',1)[0];keys[p]=keys.get(p,0)+1
phases={}
for ph,se in d.execute('select phase,settled from deliveries'):
 phases[ph+('/settled' if se else '/unsettled')]=phases.get(ph+('/settled' if se else '/unsettled'),0)+1
d.close()
print(json.dumps({'release':c.get('release'),'profile':e.get('WORKER_ASSIGNMENT_PROFILE',''),
 'personas':e.get('OPERATOR_REHEARSAL_PERSONAS_ENABLED',''),'status':status,'stateKeys':keys,'deliveries':phases}))
"""

RUNTIME_BACKUP = r"""
import json,os,pathlib,pwd,subprocess,sys
target=pathlib.Path(sys.argv[1]);target.parent.mkdir(parents=True,exist_ok=True)
a=pwd.getpwnam('hermes');os.chown(target.parent,a.pw_uid,a.pw_gid)
env=dict(os.environ,HERMES_HOME='/data/hermes',HOME='/data/hermes/home')
r=subprocess.run(['/opt/hermes/bin/hermes','backup','-o',str(target),'-k','0'],env=env,capture_output=True,text=True,
 timeout=600,user=a.pw_uid,group=a.pw_gid,extra_groups=[])
if r.returncode or not target.is_file():raise SystemExit('hermes backup failed: '+(r.stdout+r.stderr)[-2000:])
print(json.dumps({'path':str(target),'bytes':target.stat().st_size}))
"""

GATEWAY_BACKUP = r"""
import json,sqlite3,sys
c=json.load(open('/app/.sandbox-service.json'));e=c['environment']
s=sqlite3.connect(e['TRANSPORT_JOURNAL_PATH']);t=sqlite3.connect(sys.argv[1])
s.backup(t);t.close();s.close();print(json.dumps({'path':sys.argv[1]}))
"""

# Runs as root with Hermes quiescent; the Hermes process is restarted afterwards so no cached state survives.
RUNTIME_RESET = r"""
import json,os,pathlib,pwd,shutil,sqlite3,subprocess,sys
home=pathlib.Path('/data/hermes');stamp=sys.argv[1]
archive=pathlib.Path('/data/otto-archive')/stamp;archive.mkdir(parents=True,exist_ok=True);os.chmod(archive.parent,0o700)
moved=[]
def move(p):
 if p.exists() or p.is_symlink():
  dest=archive/p.relative_to(home);dest.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(p),str(dest));moved.append(p.relative_to(home).as_posix())
private=home/'skills/private'
for p in (sorted(private.iterdir()) if private.is_dir() else []):move(p)
candidates=home/'skills/candidates'
for p in (sorted(candidates.iterdir()) if candidates.is_dir() else []):move(p)
workspace=home/'workspace'
for p in (sorted(workspace.iterdir()) if workspace.is_dir() else []):move(p)
move(home/'skills/.curator_ledger.jsonl')
move(home/'skills/.usage.json')
for p in sorted((home/'pending/skills').glob('*.json')):move(p)
learning=home/'learning'
for p in (sorted(learning.iterdir()) if learning.is_dir() else []):move(p)
for p in sorted((home/'sessions').glob('request_dump_*')):move(p)
memories=home/'memories';cleared=[]
for p in (sorted(memories.iterdir()) if memories.is_dir() else []):
 if p.is_file() and p.suffix=='.md':
  dest=archive/p.relative_to(home);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest);p.write_text('');cleared.append(p.name)
a=pwd.getpwnam('hermes');deleted=[];failed={}
db=home/'state.db'
if db.exists():
 d=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True);ids=[r[0] for r in d.execute('select id from sessions')];d.close()
 env=dict(os.environ,HERMES_HOME=str(home),HOME=str(home/'home'))
 for i in ids:
  r=subprocess.run(['/opt/hermes/bin/hermes','sessions','delete',i,'--yes'],env=env,capture_output=True,text=True,
   timeout=300,user=a.pw_uid,group=a.pw_gid,extra_groups=[])
  if r.returncode:failed[i]=(r.stdout+r.stderr)[-600:]
  else:deleted.append(i)
play=pathlib.Path(sys.argv[2])
for p in (private,candidates,workspace,play.parent):
 p.mkdir(parents=True,exist_ok=True)
pathlib.Path(sys.argv[3]).replace(play)
for p in (private,candidates,workspace,play.parent,play):os.chown(p,a.pw_uid,a.pw_gid)
os.chmod(play,0o600)
print(json.dumps({'archive':str(archive),'moved':moved,'clearedMemories':cleared,'deletedSessions':deleted,
 'failedSessions':failed}))
"""

GATEWAY_RESET = r"""
import json,sqlite3
c=json.load(open('/app/.sandbox-service.json'));e=c['environment']
d=sqlite3.connect(e['TRANSPORT_JOURNAL_PATH'])
n=d.execute("delete from conversation_state where key like 'teams:%'").rowcount;d.commit();d.close()
print(json.dumps({'deletedTeamsContext':n}))
"""


RUNTIME_API = r"""
import json,sys,urllib.error,urllib.request
e=json.load(open('/app/.sandbox-service.json'))['environment']
method,path,body=sys.argv[1],sys.argv[2],(json.loads(sys.argv[3]) if len(sys.argv)>3 and sys.argv[3] else None)
q=urllib.request.Request('http://127.0.0.1:8642'+path,data=None if body is None else json.dumps(body).encode(),
 headers={'X-Autopilot-Key':e['API_SERVER_KEY'],'Authorization':'Bearer '+e['API_SERVER_KEY'],'Content-Type':'application/json'},method=method)
try:
 with urllib.request.urlopen(q,timeout=120) as r:print(json.dumps({'status':r.status,'body':json.load(r)}))
except urllib.error.HTTPError as x:print(json.dumps({'status':x.code,'body':x.read().decode()[:2000]}))
"""

RUNTIME_CRON_OUTPUT = r"""
import json,pathlib,sys
d=pathlib.Path('/data/hermes/cron/output')/sys.argv[1]
files=sorted((f for f in d.rglob('*') if f.is_file()),key=lambda f:f.stat().st_mtime) if d.is_dir() else []
last=files[-1] if files else None
print(json.dumps({'runs':len(files),'file':last.name if last else None,
 'output':last.read_text(errors='replace')[-20000:] if last else None}))
"""

RUNTIME_CRON_REFERENCES = r"""
import json,pathlib
p=pathlib.Path('/data/hermes/local/delivery-references.json')
refs=json.loads(p.read_text()) if p.is_file() else {}
def person(v):
 c=(v.get('conversation') or {}).get('conversation_reference') or {}
 u=c.get('user') or {}
 return {'name':u.get('name'),'aadObjectId':u.get('aadObjectId'),
  'conversationType':(c.get('conversation') or {}).get('conversationType')}
print(json.dumps({'references':[{'referenceKey':k,'boundary':v.get('boundary'),**person(v)} for k,v in refs.items() if isinstance(v,dict)]}))
"""

SEASON_CHECK_PROMPT = (
    "Weekly budget season check for Adam Vávra, Group CFO. Use the budget-season-fy2027 skill and act as Otto doing "
    "your own coordination work: on_behalf_of is 'otto'. Call season_overview, then do what the weekly cadence row of "
    "the current season week asks. Send entity owners reminders or queries through send_entity_message only when they "
    "are due. In week 7, build the board pack draft from board_pack_data. Report to the Group CFO only what needs his "
    "attention or decision this week, in a short message."
)


def outputs() -> dict[str, Any]:
    data = load_json(runtime_outputs_path("hermes", "otto"))
    if data["worker_id"] != "otto":
        raise ValueError("Captured deployment belongs to another worker.")
    return data


@contextmanager
def sandbox(role: str, *, wake: bool = True) -> Iterator[Any]:
    data = outputs()
    group_role, id_key, _ = ROLES[role]
    with AzureCliCredential(tenant_id=data["tenant_id"], process_timeout=150) as credential:
        with SandboxGroupClient(
            endpoint_for_region(data["sandbox_location"]), credential,
            subscription_id=data["subscription_id"], resource_group=data["resource_group_name"],
            sandbox_group=data["sandbox_groups"][group_role]["name"], audience=DISK_IMAGE_AUDIENCE, read_timeout=900,
        ) as group:
            client = group.get_sandbox_client(data[id_key])
            if wake:
                client.ensure_running(timeout=600)
            yield client


def run_json(client: Any, script: str, *arguments: str, operation: str) -> dict[str, Any]:
    command = "python3 -c " + shlex.quote(script.strip()) + "".join(" " + shlex.quote(a) for a in arguments)
    result = client.exec(command)
    require_exec_success(result, operation)
    return json.loads(result.stdout.strip().splitlines()[-1])


def restart_service(client: Any, port: int) -> None:
    """Restart the service process with an unchanged configuration (the bootstrap restarts on any content change)."""
    previous = json.loads(client.read_file(SERVICE_CONFIG_PATH))
    content = json.dumps(previous, sort_keys=True)
    current = client.read_file(SERVICE_CONFIG_PATH).decode()
    client.write_file(SERVICE_CONFIG_PATH + ".next", content + ("\n" if current == content else ""), mode="0600")
    require_exec_success(client.exec(f"mv {SERVICE_CONFIG_PATH}.next {SERVICE_CONFIG_PATH}"), "Restarting service")
    require_service_health(client, port, previous["release"])


def season(arguments: list[str]) -> dict[str, Any]:
    with sandbox("private-mcp") as client:
        config = json.loads(client.read_file(SERVICE_CONFIG_PATH))
        if config["command"][-1] != "budget_planning_mcp.server":
            raise RuntimeError("The private MCP sandbox does not run the budget-planning MCP.")
        environment = " ".join(
            f"{name}={shlex.quote(config['environment'][name])}"
            for name in ("BUDGET_STATE_PATH",) if name in config["environment"]
        )
        command = (
            f"cd {shlex.quote(config['workingDirectory'])} && env {environment} "
            + " ".join(shlex.quote(part) for part in (config["command"][0], *ADMIN, *arguments))
        )
        result = client.exec("sh -c " + shlex.quote(command))
        require_exec_success(result, "Budget season admin")
    text = result.stdout.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"output": text}


def inspect() -> dict[str, Any]:
    result: dict[str, Any] = {"observedUtc": datetime.now(timezone.utc).isoformat()}
    with sandbox("runtime") as client:
        result["runtime"] = run_json(client, RUNTIME_INSPECT, operation="Inspecting runtime")
    with sandbox("gateway") as client:
        result["gateway"] = run_json(client, GATEWAY_INSPECT, operation="Inspecting gateway")
    try:
        result["season"] = season(["status"])
    except (RuntimeError, KeyError) as error:
        result["season"] = {"unavailable": str(error)}
    return result


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def backup() -> dict[str, Any]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = ROOT / "backups" / stamp
    directory.mkdir(parents=True, exist_ok=False)
    receipt: dict[str, Any] = {"createdUtc": datetime.now(timezone.utc).isoformat(), "files": {}}
    with sandbox("runtime") as client:
        remote = f"/tmp/otto-backup/{stamp}.zip"
        run_json(client, RUNTIME_BACKUP, remote, operation="Native Hermes backup")
        data = client.read_file(remote)
        require_exec_success(client.exec("rm -f " + shlex.quote(remote)), "Removing staged backup")
        (directory / "hermes-home.zip").write_bytes(data)
        receipt["files"]["hermes-home.zip"] = {"bytes": len(data), "sha256": sha256(data)}
    with sandbox("gateway") as client:
        remote = f"/tmp/gateway-journal-{stamp}.sqlite3"
        run_json(client, GATEWAY_BACKUP, remote, operation="Gateway journal backup")
        data = client.read_file(remote)
        require_exec_success(client.exec("rm -f " + shlex.quote(remote)), "Removing staged journal")
        (directory / "gateway-journal.sqlite3").write_bytes(data)
        receipt["files"]["gateway-journal.sqlite3"] = {"bytes": len(data), "sha256": sha256(data)}
    try:
        exported = season(["export"])
        (directory / "budget-season-export.json").write_text(json.dumps(exported, indent=2), encoding="utf-8")
        receipt["files"]["budget-season-export.json"] = {"included": True}
    except (RuntimeError, KeyError) as error:
        receipt["files"]["budget-season-export.json"] = {"included": False, "reason": str(error)}
    (directory / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return {"directory": str(directory), **receipt}


def require_backup(path: Path, now: datetime | None = None) -> dict[str, Any]:
    receipt = json.loads((path / "receipt.json").read_text(encoding="utf-8"))
    created = datetime.fromisoformat(receipt["createdUtc"])
    if (now or datetime.now(timezone.utc)) - created > BACKUP_MAX_AGE:
        raise RuntimeError("The backup is too old for a reset; take a fresh backup first.")
    for name in ("hermes-home.zip", "gateway-journal.sqlite3"):
        entry = receipt["files"][name]
        if sha256((path / name).read_bytes()) != entry["sha256"]:
            raise RuntimeError(f"Backup file {name} does not match its receipt.")
    return receipt


def reset(backup_dir: Path) -> dict[str, Any]:
    require_backup(backup_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    playbook = PLAYBOOK.read_bytes()
    result: dict[str, Any] = {"backup": str(backup_dir), "playbookSha256": sha256(playbook)}
    with sandbox("runtime") as client:
        previous = json.loads(client.read_file(SERVICE_CONFIG_PATH))
        with service_maintenance(client):
            require_quiescent_service(client, previous, ROLES["runtime"][2])
            staged = f"/tmp/playbook-{stamp}.md"
            client.write_file(staged, playbook, mode="0600")
            result["runtime"] = run_json(
                client, RUNTIME_RESET, stamp, PLAYBOOK_REMOTE, staged, operation="Resetting runtime state",
            )
            if sha256(client.read_file(PLAYBOOK_REMOTE)) != result["playbookSha256"]:
                raise RuntimeError("Installed playbook hash mismatch.")
            restart_service(client, ROLES["runtime"][2])
    with sandbox("gateway") as client:
        previous = json.loads(client.read_file(SERVICE_CONFIG_PATH))
        with service_maintenance(client):
            require_quiescent_service(client, previous, ROLES["gateway"][2])
            result["gateway"] = run_json(client, GATEWAY_RESET, operation="Resetting gateway conversation context")
            restart_service(client, ROLES["gateway"][2])
    receipt = ROOT / "resets" / f"{stamp}.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(result, indent=2), encoding="utf-8")
    if result["runtime"]["failedSessions"]:
        raise RuntimeError(f"Some Hermes sessions could not be deleted; see {receipt}.")
    return {"receipt": str(receipt), **result}


def learning(action: str, pending_id: str = "", decision: str = "") -> dict[str, Any]:
    """List staged playbook/skill changes, or decide one as the Worker owner (the Group CFO in the demo)."""
    owner = load_json(REPO_ROOT / "terraform" / "apps" / "generated.app.auto.tfvars.json").get("worker_owner_id", "")
    if action == "decide" and not (pending_id and decision in {"approve", "reject"} and owner):
        raise ValueError("decide needs a pending ID, approve or reject, and a configured worker_owner_id.")
    with sandbox("runtime") as client:
        if action == "list":
            digest = runtime_api(client, "GET", "/internal/learning/digest?limit=50&markPresented=false")
            fields = ("pendingId", "origin", "skill", "summary", "createdAt", "diff")
            return {"pending": [{key: item.get(key) for key in fields} for item in digest.get("pending") or []]}
        return runtime_api(client, "POST", "/internal/learning/pending/decide",
                           {"pendingId": pending_id, "approve": decision == "approve", "decidedBy": owner})


def runtime_api(client: Any, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    result = run_json(client, RUNTIME_API, method, path, "" if body is None else json.dumps(body),
                      operation=f"Runtime API {method} {path.split('?')[0]}")
    if result["status"] >= 400:
        raise RuntimeError(f"Runtime API {method} {path} returned {result['status']}: {result['body']}")
    return result["body"]


def cron(action: str, job_id: str = "", schedule: str = "", prompt: str = "", teams_reference: str = "") -> dict[str, Any]:
    """Operate Otto's scheduled checks: list, run one now, or manage an operator job and read its output.

    `run-now` makes the job due immediately and re-arms it in Service Bus, so it fires through the production path
    (gateway, runtime, then proactive Teams delivery when the job is bound to a Teams conversation).
    """
    if action not in {"list", "references", "create"} and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", job_id):
        raise ValueError(f"{action} needs a job ID; see `cron list`.")
    with sandbox("runtime") as client:
        if action == "list":
            return runtime_api(client, "GET", "/internal/cron/jobs")
        if action == "references":
            return run_json(client, RUNTIME_CRON_REFERENCES, operation="Reading Teams delivery references")
        if action == "output":
            return run_json(client, RUNTIME_CRON_OUTPUT, job_id, operation="Reading scheduled output")
        if action == "create":
            job = runtime_api(client, "POST", "/api/jobs", {
                "name": "Budget season check", "schedule": schedule or "0 7 * * 1",
                "prompt": prompt or SEASON_CHECK_PROMPT, "deliver": "local", "skills": ["budget-season-fy2027"],
            })["job"]
            job_id = job["id"]
            if teams_reference:
                runtime_api(client, "POST", "/internal/cron/bind-delivery",
                            {"jobIds": [job_id], "referenceKey": teams_reference})
            else:
                runtime_api(client, "POST", "/internal/cron/bind-local", {"jobIds": [job_id]})
        elif action == "run-now":
            runtime_api(client, "POST", f"/api/jobs/{job_id}/run", {})
        elif action == "delete":
            runtime_api(client, "DELETE", f"/api/jobs/{job_id}")
        reconciliation = runtime_api(client, "POST", "/internal/cron/reconcile", {})
        jobs = runtime_api(client, "GET", "/internal/cron/jobs")["jobs"]
    return {"reconcile": reconciliation, "job": next((job for job in jobs if job["id"] == job_id), None)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect")
    commands.add_parser("backup")
    resetting = commands.add_parser("reset")
    resetting.add_argument("--backup", required=True, type=Path, help="Backup directory from the backup command")
    admin = commands.add_parser("season", help="Run the budget-planning admin CLI, e.g. season set-week 3")
    admin.add_argument("arguments", nargs=argparse.REMAINDER)
    learn = commands.add_parser("learning", help="List staged learning, or decide one as the Worker owner")
    learn.add_argument("action", choices=("list", "decide"))
    learn.add_argument("pending_id", nargs="?", default="")
    learn.add_argument("decision", nargs="?", choices=("approve", "reject"))
    scheduled = commands.add_parser("cron", help="List Otto's scheduled checks, run one now, or manage an operator job")
    scheduled.add_argument("action", choices=("list", "references", "run-now", "create", "output", "delete"))
    scheduled.add_argument("job_id", nargs="?", default="")
    scheduled.add_argument("--schedule", default="", help="create: Hermes schedule (UTC), default Mondays 07:00")
    scheduled.add_argument("--prompt", default="", help="create: job prompt, default the weekly season check")
    scheduled.add_argument("--teams-reference", default="",
                           help="create: deliver to this Teams chat (see `cron references`); default local only")
    args = parser.parse_args()
    if args.command == "inspect":
        result = inspect()
    elif args.command == "backup":
        result = backup()
    elif args.command == "reset":
        result = reset(args.backup)
    elif args.command == "learning":
        result = learning(args.action, args.pending_id, args.decision or "")
    elif args.command == "cron":
        result = cron(args.action, args.job_id, args.schedule, args.prompt, args.teams_reference)
    else:
        result = season(args.arguments)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
