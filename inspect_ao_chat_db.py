import sqlite3
import json
from pathlib import Path

uri = "file:C:/Users/admin/.ao/data/ao.db?mode=ro"
connection = sqlite3.connect(uri, uri=True)
session_id = "mh-pilot-11"
client_message_id = ""
session = connection.execute(
    "select provider_conversation_id, activity_state, session_mode, model, latest_assistant_update, prompt, latest_user_prompt "
    "from sessions where id = ?", (session_id,)
).fetchone()
if not session:
    print("session_found=false")
    raise SystemExit(0)
conversation_id, activity, mode, model, latest, prompt, latest_user = session
marker = "CODEX_GPT61_SOL_READY"
print(f"session_found=true activity={activity} mode={mode} model={model or 'unset'} latest_update={bool(latest)} latest_marker={marker in (latest or '')} spawn_prompt_marker={marker in (prompt or '')} latest_user_marker={marker in (latest_user or '')}")
delivery = connection.execute(
    "select state, rejection_kind, turn_state, provider_work_started, settled_at "
    "from conversation_edit_deliveries where client_message_id = ?",
    (client_message_id,),
).fetchone()
if delivery:
    print(f"delivery state={delivery[0]} rejection={delivery[1]} turn_state={delivery[2]} provider_started={bool(delivery[3])} settled={bool(delivery[4])}")
branches = connection.execute(
    "select conversation_id, provider_conversation_id from conversation_branches where session_id = ?",
    (session_id,),
).fetchall()
if branches:
    print(f"branch_count={len(branches)}")
    print(f"branch_has_provider_conversation={any(bool(x[1]) for x in branches)}")
    for branch_conversation_id, _provider_id in branches:
        messages = connection.execute("select role, origin, text, streaming from conversation_messages where conversation_id = ? order by sequence", (branch_conversation_id,)).fetchall()
        messages_count = len(messages)
        activities_count = connection.execute("select count(*) from conversation_activities where conversation_id = ?", (branch_conversation_id,)).fetchone()[0]
        deliveries_count = connection.execute("select count(*) from conversation_edit_deliveries where conversation_id = ?", (branch_conversation_id,)).fetchone()[0]
        print(f"branch_messages={messages_count} activities={activities_count} deliveries={deliveries_count}")
        for role, origin, text, streaming in messages:
            safe_text = text or ""
            print(f"ao_message role={role} origin={origin} chars={len(safe_text)} marker={marker in safe_text} streaming={bool(streaming)}")
    if not conversation_id:
        conversation_id = branches[-1][0]
if conversation_id:
    rows = connection.execute(
        "select role, origin, text, streaming from conversation_messages where conversation_id = ? order by sequence",
        (conversation_id,),
    ).fetchall()
    for role, origin, text, streaming in rows:
        print(f"message role={role} origin={origin} chars={len(text or '')} marker={marker in (text or '')} streaming={bool(streaming)}")
    activities = connection.execute(
        "select kind, status, summary, command_output, streamed_text from conversation_activities where conversation_id = ? order by sequence",
        (conversation_id,),
    ).fetchall()
    for kind, status, summary, command_output, streamed_text in activities:
        combined = " ".join(x or "" for x in (summary, command_output, streamed_text))
        print(f"activity kind={kind} status={status} chars={len(combined)} marker={marker in combined}")
else:
    print("conversation_found=false")

catalog = connection.execute(
    "select catalog_json, binary_version, source from agent_model_catalog "
    "where agent_id = 'deepseek-harness' and project_id = 'mh-pilot' order by fetched_at desc limit 1"
).fetchone()
if catalog:
    raw_catalog, version, source = catalog
    try:
        payload = json.loads(raw_catalog)
        ids = set()
        def collect(value):
            if isinstance(value, dict):
                for key, nested in value.items():
                    if key in {'id', 'model', 'value'} and isinstance(nested, (str, int, float)):
                        ids.add(str(nested))
                    collect(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect(nested)
        collect(payload)
        selected = sorted(x for x in ids if any(name in x for name in ('tianyi', 'deepseek-v4-pro-0813', 'kimi-k3', 'glm-5.3')))
        print(f"ao_model_catalog version={version} source={source} tianyi_models={','.join(selected) if selected else 'none'}")
    except Exception:
        print("ao_model_catalog parse_failed=true")
else:
    print("ao_model_catalog found=false")

marker = "CODEX_GPT61_SOL_READY"
session_root = Path.home() / ".dsh" / "storages" / "session_projcache" / "sessions"
for path in session_root.glob("*.json"):
    raw = path.read_text(encoding="utf-8", errors="replace")
    if marker not in raw:
        continue
    try:
        payload = json.loads(raw)
    except Exception:
        print(f"dsh_session_file={path.name} json_parse_failed=true")
        continue
    print(f"dsh_session_file={path.name} marker_present=true")
    def schema(value, prefix="root", depth=0):
        if depth > 4:
            return
        if isinstance(value, dict):
            print(f"dsh_shape path={prefix} keys={','.join(value.keys())[:220]}")
            for key, nested in value.items():
                if key in {"event", "type", "role", "provider", "model", "status", "stopReason", "kind", "name", "errorCode"} and isinstance(nested, str):
                    print(f"dsh_field path={prefix}.{key} value={nested[:80]}")
                elif isinstance(nested, (dict, list)):
                    schema(nested, f"{prefix}.{key}", depth + 1)
        elif isinstance(value, list):
            print(f"dsh_shape path={prefix} list_count={len(value)}")
            for index, nested in enumerate(value[:8]):
                if isinstance(nested, (dict, list)):
                    schema(nested, f"{prefix}[{index}]", depth + 1)
    schema(payload)
    def walk(value, role="unknown"):
        if isinstance(value, dict):
            role = value.get("role", role)
            for key, nested in value.items():
                if key == "text" and isinstance(nested, str):
                    print(f"dsh_message role={role} chars={len(nested)} marker={marker in nested}")
                else:
                    walk(nested, role)
        elif isinstance(value, list):
            for nested in value:
                walk(nested, role)
    walk(payload)
