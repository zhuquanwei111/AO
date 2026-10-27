"""屏识助手单场取数组件；单一GLM路由，业务运行不使用AO或Codex。"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'fixed-recap-prototype'))
from core import RecapError
from flow import Store,Runner
from glm_runtime import GLMRuntime,MODEL,require_gate_a,require_sdk_runtime

APP_SOURCES={'ios_app','android_app'}


def default_data_root():
    local_app_data=os.environ.get('LOCALAPPDATA')
    if local_app_data:
        base=Path(local_app_data)
    else:
        try: base=Path.home()/'.local'/'share'
        except RuntimeError: base=Path(tempfile.gettempdir())
    return base/'ScreenAssistant'/'backstage-recap'


def require_external_path(path):
    resolved=Path(path).resolve()
    try: resolved.relative_to(ROOT.resolve())
    except ValueError: return resolved
    raise RecapError('runtime_path_inside_install')


def operation_exit_code(command,record):
    return 2 if command in ('start','stop') and record['status'] in ('needs_input','failed') else 0


def validate_app_source(record,source):
    saved=record.get('start_signal_source')
    if (saved in APP_SOURCES or source in APP_SOURCES) and saved!=source:
        raise RecapError('signal_source_conflict')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('start','stop','status','result'))
    parser.add_argument('--task'); parser.add_argument('--account'); parser.add_argument('--session-id')
    parser.add_argument('--source',choices=('user_chat','test_simulated','ios_app','android_app'),default='user_chat')
    data_root=default_data_root()
    parser.add_argument('--data',type=Path,
        default=Path(os.environ.get('BACKSTAGE_CLI_DATA') or data_root/'records'))
    parser.add_argument('--models',type=Path,
        default=Path(os.environ.get('BACKSTAGE_CLI_MODELS') or data_root/'models'))
    parser.add_argument('--scratch',type=Path,
        default=Path(os.environ.get('BACKSTAGE_CLI_SCRATCH') or data_root/'scratch'))
    parser.add_argument('--gate-a',type=Path,
        default=Path(os.environ.get('BACKSTAGE_CLI_GATE_A') or data_root/'gate-a.json'))
    parser.add_argument('--desktop-authorized',action='store_true')
    args=parser.parse_args()
    if args.command!='start' and not args.task: parser.error('需要--task')
    if args.command=='start' and not args.account: parser.error('start需要抖音号--account')
    args.data=require_external_path(args.data)
    args.models=require_external_path(args.models)
    args.scratch=require_external_path(args.scratch)
    args.gate_a=require_external_path(args.gate_a)
    began=time.monotonic(); store=Store(args.data)
    if args.command in ('status','result'):
        record=store.get(args.task)
    else:
        if not args.desktop_authorized: raise RecapError('current_desktop_authorization_required')
        task=args.task or 'local-'+uuid.uuid4().hex; store.path(task)
        if args.command=='stop':
            validate_app_source(store.get(task),args.source)
            record=store.stop_signal(task,args.source)
            if record['status'] in ('captured_locally','partial','failed') or not record['binding_evidence']:
                if record['status'] not in ('captured_locally','partial','failed'):
                    record=Runner(store,None).finalize_stopped_unbound(task)
                print(json.dumps({'task_ref':str(store.path(task)),'record':record},ensure_ascii=False))
                return operation_exit_code(args.command,record)
        else:
            record,created=store.create(task,args.account,args.session_id,args.source)
            validate_app_source(record,args.source)
            if not created:
                print(json.dumps({'task_ref':str(store.path(task)),'record':record},ensure_ascii=False))
                return operation_exit_code(args.command,record)
            store.update(task,execution_route='tianyi_agents_sdk_fixed_steps',model=MODEL,
                         normal_path_llm_calls=0,llm_runtime_invocations=0)
        try:
            require_gate_a(args.gate_a)
            require_sdk_runtime()
        except ValueError as error:
            record=store.update(task,status='needs_input',reason=str(error))
            print(json.dumps({'task_ref':str(store.path(task)),'record':record},ensure_ascii=False)); return 2
        if not os.environ.get('TIANYI_API_KEY'):
            record=store.update(task,status='needs_input',reason='missing_current_process_tianyi_key')
            print(json.dumps({'task_ref':str(store.path(task)),'record':record},ensure_ascii=False)); return 2
        from glm_vision import GLMVision
        from glm_desktop import GLMDesktop
        from window_probe import Probe
        from ocr_local import LocalOCR
        from transport import MCP
        class StartedStore(Store):
            def create(self,*values):
                return (self.get(task),True) if args.command=='start' else super().create(*values)
        binding=record.get('binding_evidence') or {}
        runtime=GLMRuntime(args.scratch/task/args.command,{'phase':args.command,
            'douyin_account':record['douyin_account'],
            'bound_room_id':binding.get('room_id'),'bound_at':binding.get('observed_at')})
        mcp=MCP(os.environ.get('BACKSTAGE_MCP_CLIENT_PYTHON',''),
                ROOT/'windows-mcp/mcp_stdio_client.py',
                args.scratch/task/'mcp-events.jsonl',
                args.scratch/task/'observations')
        try:
            desktop=GLMDesktop(mcp,Probe(),LocalOCR(args.models),store.root/(task+'-evidence'),
                'https://union.bytedance.com/open/portal/index',vision=GLMVision(runtime))
            runner=Runner(StartedStore(store.root),desktop)
            record=runner.start(task,args.account,args.session_id,args.source) if args.command=='start' else runner.stop(task,args.source)
        except Exception as error:
            record=store.update(task,status='needs_input',reason=error.reason if isinstance(error,RecapError) else 'dependency_or_runtime_error')
        finally:
            cleanup_confirmed=False
            try: cleanup_confirmed=mcp.close() is True
            except Exception: mcp.note('mcp_cleanup_unconfirmed')
            old=store.get(task)
            record=store.update(task,llm_runtime_invocations=old.get('llm_runtime_invocations',0)+runtime.calls,
                normal_path_llm_calls=(old['normal_path_llm_calls']+runtime.http_requests)
                    if old.get('normal_path_llm_calls') is not None and runtime.http_requests is not None else None,
                model_call_count_semantics='actual HTTP requests counted by the SDK client hook; HTTP retry disabled')
            if args.command=='start':
                record=Runner(store,None).finish_unbound_execution(task,
                    cleanup_confirmed=cleanup_confirmed and runtime.calls==0)
    print(json.dumps({'task_ref':str(store.path(record['local_test_id'])),
        'operation_elapsed_ms':round((time.monotonic()-began)*1000),'record':record},ensure_ascii=False,indent=2))
    return operation_exit_code(args.command,record)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try: raise SystemExit(main())
    except RecapError as error:
        print(json.dumps({'status':'needs_input','reason':error.reason},ensure_ascii=False)); raise SystemExit(2)
