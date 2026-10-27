"""单任务状态与固定start/stop；桌面动作委托既有Windows-MCP适配。"""
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import re
import time
import uuid

from core import RecapError


def now():
    return datetime.now().astimezone().isoformat()


@contextmanager
def lock(path, wait=0):
    deadline=time.monotonic()+wait
    while True:
        try:
            fd = os.open(path, os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            if time.monotonic()>=deadline: raise RecapError('busy') from None
            time.sleep(.02)
    try:
        os.write(fd, str(os.getpid()).encode('ascii'))
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def atomic_json(path, value):
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n'
    expected = json.loads(data)
    tmp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with tmp.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if json.loads(tmp.read_text(encoding='utf-8')) != expected:
            raise RecapError('save_verification_failed')
        os.replace(tmp,path)
        if json.loads(path.read_text(encoding='utf-8')) != expected:
            raise RecapError('save_verification_failed')
    finally:
        tmp.unlink(missing_ok=True)


class Store:
    def __init__(self, root):
        self.root=Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True)

    def path(self, task):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',task):
            raise RecapError('invalid_local_task_id')
        return self.root/(task+'.json')

    def get(self, task):
        path=self.path(task)
        if not path.exists():
            raise RecapError('unknown_task')
        return json.loads(path.read_text(encoding='utf-8'))

    def create(self, task, account, session_id, source):
        if not re.fullmatch(r'[\w.-]{1,64}',account):
            raise RecapError('invalid_account')
        if session_id is not None and (not isinstance(session_id,str) or len(session_id)>256):
            raise RecapError('invalid_session_id')
        with lock(self.root/'state.lock',wait=2):
            path=self.path(task)
            if path.exists():
                old=self.get(task)
                if old['douyin_account'] != account or old['session_id'] != session_id:
                    raise RecapError('task_identity_conflict')
                return old,False
            active=self.root/'active.json'
            if active.exists():
                previous=self.get(json.loads(active.read_text(encoding='utf-8'))['task'])
                if previous['status'] not in ('captured_locally','partial','failed'):
                    raise RecapError('busy')
            value={'local_test_id':task,'session_id':session_id,'app_integrated':False,
                   'douyin_account':account,'status':'binding','phase':'binding',
                   'start_signal_source':source,'start_signal_received_at':now(),
                   'stop_signal_received':False,'metrics':None,'binding_evidence':None,
                   'normal_path_llm_calls':0,'events':[]}
            atomic_json(path,value)
            atomic_json(active,{'task':task})
            return value,True

    def update(self,task,**fields):
        with lock(self.root/'state.lock',wait=2):
            value=self.get(task)
            value.update(fields)
            atomic_json(self.path(task),value)
            return value

    def stop_signal(self, task, source):
        with lock(self.root/'state.lock',wait=2):
            value=self.get(task)
            if not value['stop_signal_received']:
                value.update(stop_signal_received=True,stop_signal_source=source,
                             stop_signal_received_at=now())
                atomic_json(self.path(task),value)
            return value


class Runner:
    def __init__(self,store,desktop):
        self.store,self.desktop=store,desktop

    def start(self,task,account,session_id=None,source='user_chat'):
        record,created=self.store.create(task,account,session_id,source)
        if not created:
            return record
        began=time.monotonic()
        try:
            with lock(self.store.root/'desktop.lock'):
                binding=self.desktop.bind(account)
                require_binding_account(binding,account)
                self.store.update(task,binding_evidence=binding,room_id=binding['room_id'],
                                  binding_observed_at=now(),status='waiting_for_stop',
                                  phase='waiting_for_stop',enter_elapsed_ms=round((time.monotonic()-began)*1000))
                if self.store.get(task)['stop_signal_received']:
                    return self._capture(task)
                return self.store.get(task)
        except RecapError as error:
            return self._fail(task,error.reason)
        except Exception:
            return self._fail(task,'unexpected_error',failed=True)

    def stop(self,task,source='user_chat'):
        record=self.store.stop_signal(task,source)
        if record['status'] in ('captured_locally','partial','failed'):
            return record
        if record.get('reason','').endswith('outcome_unknown'):
            return record
        if record['status']=='capturing':
            if (self.store.root/'desktop.lock').exists(): return record
            return self._fail(task,'capture_outcome_unknown')
        if not record['binding_evidence']:
            return self.finalize_stopped_unbound(task)
        try:
            # 仅等待正在收尾的本任务短段；不是多主播任务队列。
            with lock(self.store.root/'desktop.lock',wait=2):
                if self.store.get(task)['status'] in ('captured_locally','partial','failed'):
                    return self.store.get(task)
                return self._capture(task)
        except RecapError as error:
            if error.reason=='busy':
                return self.store.get(task)
            return self._fail(task,error.reason)
        except Exception:
            return self._fail(task,'unexpected_error',failed=True)

    def _capture(self,task):
        began=time.monotonic()
        record=self.store.update(task,status='capturing',phase='capturing',capture_handling_started_at=now())
        result=self.desktop.capture(record['binding_evidence'])
        return self.store.update(task,**result,
            status='partial' if result.get('missing_fields') else 'captured_locally',phase='finished',
            capture_before_final_save_ms=round((time.monotonic()-began)*1000),
            window_cleanup='preserved_by_prototype',
            value_semantics='stop信号触发后读取的本场左侧汇总，不确认平台结算；测试信号只代表测试快照')

    @staticmethod
    def _known_unbound_failure(record):
        # 仅采用本轮已核过的绑定前失败；不是按字符串后缀猜测全部异常。
        return (record.get('status')=='needs_input' and record.get('phase')=='binding'
                and record.get('binding_evidence') is None
                and record.get('reason')=='edge_launch_unconfirmed')

    def finish_unbound_execution(self,task,*,cleanup_confirmed):
        """仅由执行者在同步调用返回且MCP正常关闭后调用，不迁移旧记录。"""
        if cleanup_confirmed is not True:
            return self.store.get(task)
        try:
            with lock(self.store.root/'desktop.lock',wait=2):
                record=self.store.get(task)
                if not self._known_unbound_failure(record): return record
                event={'event':'unbound_execution_closed','local_test_id':task,
                       'session_id':record['session_id'],
                       'douyin_account':record['douyin_account'],
                       'start_signal_source':record['start_signal_source'],
                       'reason':record['reason'],'mcp_closed_normally':True,'at':now()}
                events=record.get('events',[])
                if not self._has_closed_execution(record):
                    self.store.update(task,events=[*events,event])
                return self._finalize_stopped_unbound_locked(task)
        except RecapError as error:
            if error.reason=='busy': return self.store.get(task)
            raise

    @staticmethod
    def _has_closed_execution(record):
        return any(event.get('event')=='unbound_execution_closed'
                   and event.get('local_test_id')==record['local_test_id']
                   and event.get('session_id')==record['session_id']
                   and event.get('douyin_account')==record['douyin_account']
                   and event.get('start_signal_source')==record['start_signal_source']
                   and event.get('reason')==record.get('reason')
                   and event.get('mcp_closed_normally') is True
                   for event in record.get('events',[]) if isinstance(event,dict))

    def finalize_stopped_unbound(self,task):
        try:
            with lock(self.store.root/'desktop.lock',wait=2):
                return self._finalize_stopped_unbound_locked(task)
        except RecapError as error:
            if error.reason=='busy': return self.store.get(task)
            raise

    def _finalize_stopped_unbound_locked(self,task):
        record=self.store.get(task)
        if (not self._known_unbound_failure(record) or not self._has_closed_execution(record)
                or record.get('stop_signal_received') is not True
                or record.get('stop_signal_source')!=record.get('start_signal_source')):
            return record
        return self._fail(task,record['reason'],failed=True,
            last_failure_at=record.get('last_failure_at'),
            events=[*record.get('events',[]),{'event':'stopped_unbound_failed','at':now()}])

    def _fail(self,task,reason,failed=False,**fields):
        return self.store.update(task,status='failed' if failed else 'needs_input',reason=reason,
                                 **{'last_failure_at':now(),**fields})


def require_binding_account(binding,account):
    if binding.get('douyin_account')!=account or not binding.get('room_id'):
        raise RecapError('wrong_identity')
