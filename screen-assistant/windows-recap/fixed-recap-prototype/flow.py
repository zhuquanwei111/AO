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
    fd=os.open(path.with_name(path.name+'.lease'),os.O_CREAT|os.O_RDWR,0o600)
    try:
        if os.name=='nt':
            import msvcrt
            acquire=lambda:msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            acquire=lambda:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            try:
                acquire()
                break
            except OSError:
                if time.monotonic()>=deadline: raise RecapError('busy') from None
                time.sleep(.02)
        yield
    finally:
        # 关闭描述符即释放系统锁；进程被终止也能释放，不删除文件避免换锁竞态。
        os.close(fd)


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
                index=json.loads(active.read_text(encoding='utf-8'))
                if index.get('task') is None:
                    if not self._admin_release_valid(index): raise RecapError('busy')
                else:
                    previous=self.get(index['task'])
                    terminal=previous['status'] in ('captured_locally','partial','failed')
                    binding_failure=(previous['status']=='failed' and previous.get('phase')=='binding')
                    if (not terminal or binding_failure) and not Runner.is_unbound_released(previous):
                        raise RecapError('busy')
            value={'local_test_id':task,'session_id':session_id,'app_integrated':False,
                   'douyin_account':account,'status':'binding','phase':'binding',
                   'start_signal_source':source,'start_signal_received_at':now(),
                   'stop_signal_received':False,'metrics':None,'binding_evidence':None,
                   'normal_path_llm_calls':0,'events':[]}
            atomic_json(path,value)
            atomic_json(active,{'task':task})
            return value,True

    def _admin_release_valid(self,index):
        if set(index)!={'task','admin_release'} or index.get('task') is not None: return False
        release=index.get('admin_release')
        if not isinstance(release,dict) or release.get('task')!='1749b7977a99471db445c195bb8efe70': return False
        if not isinstance(release.get('recovery_id'),str) or not re.fullmatch(r'[0-9a-f]{32}',release['recovery_id']): return False
        try: record=self.get(release['task'])
        except (RecapError,KeyError,ValueError): return False
        if (record.get('local_test_id')!=release['task']
                or record.get('session_id')!='d2407d04df6e45799d1f36795aac31af'
                or record.get('status')!='needs_input' or record.get('reason')!='visual_address_unconfirmed'
                or record.get('phase')!='binding'
                or record.get('start_signal_received_at')!='2026-11-03T11:48:41.202482+08:00'
                or record.get('binding_evidence') is not None or record.get('stop_signal_received') is not True
                or record.get('start_signal_source')!='android_app' or record.get('stop_signal_source')!='android_app'):
            return False
        return any(event.get('event')=='admin_abandoned_after_remote_stop'
                   and event.get('recovery_id')==release.get('recovery_id')
                   and event.get('local_test_id')==release['task']
                   and event.get('session_id')==record.get('session_id')
                   and event.get('historical_cleanup')=='unknown'
                   and event.get('current_owners')=='verified_absent'
                   and event.get('remote_created_at_ms')==1793677720507
                   and event.get('remote_stopped_at_ms')==1793677931394
                   and event.get('remote_received_at_ms')==1793677932615
                   and isinstance(event.get('at'),str) and bool(event['at'])
                   for event in record.get('events',[]) if isinstance(event,dict))

    def admin_abandon_verified(self,expected_start,remote_check,owners_clear,global_lock):
        """仅恢复已明确授权的单场占位；不推断历史正常关闭。"""
        task='1749b7977a99471db445c195bb8efe70'
        session='d2407d04df6e45799d1f36795aac31af'
        expected_remote={'task_id':task,'session_id':session,'status':'needs_input',
                         'start_signal_source':'android_app','stop_signal_source':'android_app',
                         'created_at_ms':1793677720507,'stopped_at_ms':1793677931394,
                         'received_at_ms':1793677932615,'result_exists':True,'account_matches':True,
                         'active_claimed_count':0}
        if expected_start!='2026-11-03T11:48:41.202482+08:00':
            raise RecapError('admin_recovery_identity_mismatch')
        with global_lock():
            with lock(self.root/'desktop.lock'):
                with lock(self.root/'state.lock'):
                    active=self.root/'active.json'
                    if not active.exists(): raise RecapError('admin_recovery_active_mismatch')
                    index=json.loads(active.read_text(encoding='utf-8'))
                    if index.get('task') is None and self._admin_release_valid(index):
                        return 'already_recovered'
                    if index.get('task')!=task: raise RecapError('admin_recovery_active_mismatch')
                    record=self.get(task)
                    if (record.get('local_test_id')!=task or record.get('session_id')!=session
                            or record.get('start_signal_received_at')!=expected_start
                            or record.get('status')!='needs_input' or record.get('phase')!='binding'
                            or record.get('reason')!='visual_address_unconfirmed' or record.get('binding_evidence') is not None
                            or record.get('start_signal_source')!='android_app' or record.get('stop_signal_source')!='android_app'
                            or record.get('stop_signal_received') is not True):
                        raise RecapError('admin_recovery_record_mismatch')
                    if owners_clear() is not True: raise RecapError('admin_recovery_owner_unknown')
                    proof=remote_check()
                    if (not isinstance(proof,dict) or any(proof.get(key)!=value for key,value in expected_remote.items())
                            or type(proof.get('active_claimed_count')) is not int):
                        raise RecapError('admin_recovery_remote_mismatch')
                    backup=self.root/('admin-recovery-'+task+'.json')
                    if not backup.exists(): atomic_json(backup,{'record':record,'active':index})
                    else:
                        saved=json.loads(backup.read_text(encoding='utf-8'))
                        if not isinstance(saved,dict): raise RecapError('admin_recovery_backup_mismatch')
                        saved_record=saved.get('record',{})
                        stable=('local_test_id','session_id','douyin_account','status','phase','reason',
                                'start_signal_received_at','start_signal_source','stop_signal_source',
                                'stop_signal_received','binding_evidence','metrics')
                        if (not isinstance(saved_record,dict) or any(saved_record.get(k)!=record.get(k) for k in stable)
                                or saved.get('active',{}).get('task')!=task):
                            raise RecapError('admin_recovery_backup_mismatch')
                    events=record.get('events',[])
                    prior=[event for event in events if isinstance(event,dict) and event.get('event')=='admin_abandoned_after_remote_stop']
                    if prior:
                        if len(prior)!=1 or prior[0].get('session_id')!=session: raise RecapError('admin_recovery_audit_mismatch')
                        recovery_id=prior[0]['recovery_id']
                    else:
                        recovery_id=uuid.uuid4().hex
                        record['events']=[*events,{'event':'admin_abandoned_after_remote_stop','recovery_id':recovery_id,
                            'local_test_id':task,'session_id':session,'at':now(),'historical_cleanup':'unknown',
                            'current_owners':'verified_absent',
                            'remote_created_at_ms':proof['created_at_ms'],'remote_stopped_at_ms':proof['stopped_at_ms'],
                            'remote_received_at_ms':proof['received_at_ms']}]
                        atomic_json(self.path(task),record)
                    atomic_json(active,{'task':None,'admin_release':{'task':task,'recovery_id':recovery_id}})
                    return 'recovered'

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
                if (value.get('execution_closed') is True and source==value.get('start_signal_source')
                        and value.get('status') in ('needs_input','failed')):
                    value.update(status='failed',phase='finished')
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
            # 仅明确业务判断用旧协议终态承载；其余异常仍需处理。
            settled=(not error.reason.endswith('outcome_unknown') and error.reason!='agent_worker_no_receipt')
            return self._fail(task,error.reason,failed=error.reason in ('not_live','account_not_found'),
                              binding_execution_returned=settled)
        except Exception:
            return self._fail(task,'unexpected_error',failed=True)

    def stop(self,task,source='user_chat'):
        record=self.store.stop_signal(task,source)
        if record['status']=='failed' and not record.get('binding_evidence') and self._known_unbound_failure(record):
            return self.finalize_stopped_unbound(task)
        if record['status'] in ('captured_locally','partial','failed'):
            return record
        if record.get('reason','').endswith('outcome_unknown'):
            return record
        if record['status']=='capturing':
            try:
                with lock(self.store.root/'desktop.lock'):
                    return self._fail(task,'capture_outcome_unknown')
            except RecapError as error:
                if error.reason=='busy': return record
                raise
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
        result.setdefault('window_cleanup','preserved_by_prototype')
        return self.store.update(task,**result,
            status='partial' if result.get('missing_fields') else 'captured_locally',phase='finished',
            capture_before_final_save_ms=round((time.monotonic()-began)*1000),
            value_semantics='stop信号触发后读取的本场左侧汇总，不确认平台结算；测试信号只代表测试快照')

    @staticmethod
    def _known_unbound_failure(record):
        # 仅同步返回的绑定前失败，旧版特定已知失败保留兼容。
        return (record.get('status') in ('needs_input','failed') and record.get('phase')=='binding'
                and record.get('binding_evidence') is None
                and isinstance(record.get('start_signal_received_at'),str)
                and bool(record['start_signal_received_at'])
                and (record.get('binding_execution_returned') is True
                     or record.get('reason')=='edge_launch_unconfirmed')
                and isinstance(record.get('reason'),str) and bool(record['reason'])
                and not record['reason'].endswith('outcome_unknown')
                and record['reason']!='agent_worker_no_receipt')

    def finish_execution(self,task,*,cleanup_confirmed):
        """由当前执行者在工具及视觉进程退出后确认，不据文件存在推断存活。"""
        record=self.finish_unbound_execution(task,cleanup_confirmed=cleanup_confirmed)
        if (cleanup_confirmed is not True or record.get('status') not in ('needs_input','failed')
                or record.get('reason','').endswith('outcome_unknown')
                or record.get('reason')=='agent_worker_no_receipt'):
            return record
        with lock(self.store.root/'desktop.lock',wait=2):
            fields={'execution_closed':True}
            if record.get('stop_signal_received') is True and record.get('stop_signal_source')==record.get('start_signal_source'):
                fields.update(status='failed',phase='finished')
            return self.store.update(task,**fields)

    def finish_unbound_execution(self,task,*,cleanup_confirmed):
        """仅由执行者在同步调用返回且MCP正常关闭后调用，不迁移旧记录。"""
        if cleanup_confirmed is not True:
            return self.store.get(task)
        try:
            with lock(self.store.root/'desktop.lock',wait=2):
                with lock(self.store.root/'state.lock',wait=2):
                    record=self.store.get(task)
                    active=self.store.root/'active.json'
                    if not active.exists() or json.loads(active.read_text(encoding='utf-8')).get('task')!=task:
                        return record
                    if not self._known_unbound_failure(record): return record
                    event={'event':'unbound_execution_closed','local_test_id':task,
                           'session_id':record['session_id'],
                           'douyin_account':record['douyin_account'],
                           'start_signal_source':record['start_signal_source'],
                           'start_signal_received_at':record['start_signal_received_at'],
                           'reason':record['reason'],'mcp_closed_normally':True,
                           'sdk_workers_finished':True,'at':now()}
                    events=record.get('events',[])
                    if not self._has_closed_execution(record):
                        record['events']=[*events,event]
                        atomic_json(self.store.path(task),record)
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
                   and event.get('start_signal_received_at')==record.get('start_signal_received_at')
                   and event.get('reason')==record.get('reason')
                   and event.get('mcp_closed_normally') is True
                   and event.get('sdk_workers_finished') is True
                   for event in record.get('events',[]) if isinstance(event,dict))

    @staticmethod
    def is_unbound_released(record):
        return (Runner._known_unbound_failure(record) and Runner._has_closed_execution(record)
                and record.get('stop_signal_received') is True
                and record.get('stop_signal_source')==record.get('start_signal_source')
                and any(event.get('event')=='stopped_unbound_closed'
                        and event.get('local_test_id')==record.get('local_test_id')
                        and event.get('session_id')==record.get('session_id')
                        and event.get('start_signal_received_at')==record.get('start_signal_received_at')
                        and event.get('reason')==record.get('reason')
                        for event in record.get('events',[]) if isinstance(event,dict)))

    def finalize_stopped_unbound(self,task):
        try:
            with lock(self.store.root/'desktop.lock',wait=2):
                return self._finalize_stopped_unbound_locked(task)
        except RecapError as error:
            if error.reason=='busy': return self.store.get(task)
            raise

    def _finalize_stopped_unbound_locked(self,task):
        with lock(self.store.root/'state.lock',wait=2):
            record=self.store.get(task)
            active=self.store.root/'active.json'
            if (not active.exists() or json.loads(active.read_text(encoding='utf-8')).get('task')!=task
                    or not self._known_unbound_failure(record) or not self._has_closed_execution(record)
                    or record.get('stop_signal_received') is not True
                    or record.get('stop_signal_source')!=record.get('start_signal_source')):
                return record
            if self.is_unbound_released(record): return record
            if record['reason']=='edge_launch_unconfirmed':
                record['status']='failed'
            event={'event':'stopped_unbound_closed','local_test_id':task,
                   'session_id':record['session_id'],
                   'start_signal_received_at':record['start_signal_received_at'],
                   'reason':record['reason'],'at':now()}
            record['events']=[*record.get('events',[]),event]
            atomic_json(self.store.path(task),record)
            return record

    def _fail(self,task,reason,failed=False,**fields):
        return self.store.update(task,status='failed' if failed else 'needs_input',reason=reason,
                                 **{'last_failure_at':now(),**fields})


def require_binding_account(binding,account):
    if binding.get('douyin_account')!=account or not binding.get('room_id'):
        raise RecapError('wrong_identity')
