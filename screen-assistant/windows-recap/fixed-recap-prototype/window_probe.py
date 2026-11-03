"""只读窗口元数据：唯一窗口、前台和屏幕边界；所有输入仍经Windows-MCP。"""
import ctypes
import re
from ctypes import wintypes as w
from core import RecapError


class ProcessEntry(ctypes.Structure):
    _fields_=[('dwSize',w.DWORD),('cntUsage',w.DWORD),('pid',w.DWORD),
              ('heap',ctypes.c_size_t),('module',w.DWORD),('threads',w.DWORD),
              ('parent_pid',w.DWORD),('priority',w.LONG),('flags',w.DWORD),('exe',w.WCHAR*260)]


class Probe:
    def __init__(self,mcp=None):
        self.mcp=mcp
        self.u=ctypes.WinDLL('user32',use_last_error=True)
        self.k=ctypes.WinDLL('kernel32',use_last_error=True)
        self.d=ctypes.WinDLL('dwmapi',use_last_error=True)
        self.callback=ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
        signatures=[(self.u.EnumWindows,[self.callback,w.LPARAM],w.BOOL),
            (self.u.IsWindow,[w.HWND],w.BOOL),
            (self.u.IsWindowVisible,[w.HWND],w.BOOL),(self.u.IsIconic,[w.HWND],w.BOOL),
            (self.u.GetForegroundWindow,[],w.HWND),(self.u.GetWindowRect,[w.HWND,ctypes.POINTER(w.RECT)],w.BOOL),
            (self.u.GetWindowTextW,[w.HWND,w.LPWSTR,ctypes.c_int],ctypes.c_int),
            (self.u.GetClassNameW,[w.HWND,w.LPWSTR,ctypes.c_int],ctypes.c_int),
            (self.u.GetWindow,[w.HWND,w.UINT],w.HWND),
            (self.u.GetAncestor,[w.HWND,w.UINT],w.HWND),
            (self.u.GetWindowLongW,[w.HWND,ctypes.c_int],w.LONG),
            (self.u.IsWindowEnabled,[w.HWND],w.BOOL),
            (self.u.WindowFromPoint,[w.POINT],w.HWND),
            (self.u.ScreenToClient,[w.HWND,ctypes.POINTER(w.POINT)],w.BOOL),
            (self.u.ChildWindowFromPointEx,[w.HWND,w.POINT,w.UINT],w.HWND),
            (self.u.GetLayeredWindowAttributes,[w.HWND,ctypes.POINTER(w.DWORD),ctypes.POINTER(w.BYTE),ctypes.POINTER(w.DWORD)],w.BOOL),
            (self.u.GetWindowThreadProcessId,[w.HWND,ctypes.POINTER(w.DWORD)],w.DWORD),
            (self.k.OpenProcess,[w.DWORD,w.BOOL,w.DWORD],w.HANDLE),
            (self.k.QueryFullProcessImageNameW,[w.HANDLE,w.DWORD,w.LPWSTR,ctypes.POINTER(w.DWORD)],w.BOOL),
            (self.k.GetProcessTimes,[w.HANDLE]+[ctypes.POINTER(w.FILETIME)]*4,w.BOOL),
            (self.k.CreateToolhelp32Snapshot,[w.DWORD,w.DWORD],w.HANDLE),
            (self.k.Process32FirstW,[w.HANDLE,ctypes.POINTER(ProcessEntry)],w.BOOL),
            (self.k.Process32NextW,[w.HANDLE,ctypes.POINTER(ProcessEntry)],w.BOOL),
            (self.d.DwmGetWindowAttribute,[w.HWND,w.DWORD,ctypes.c_void_p,w.DWORD],w.LONG),
            (self.k.CloseHandle,[w.HANDLE],w.BOOL)]
        for fn,args,result in signatures: fn.argtypes=args; fn.restype=result
        # 仅设置本原型进程DPI坐标模式，避免读取逻辑像素后传给物理输入。
        dpi=self.u.SetProcessDpiAwarenessContext
        dpi.argtypes=[w.HANDLE]; dpi.restype=w.BOOL
        dpi(w.HANDLE(-4))

    def process_created(self,pid):
        process=self.k.OpenProcess(0x1000,False,pid)
        if not process: return None
        try:
            times=[w.FILETIME() for _ in range(4)]
            if not self.k.GetProcessTimes(process,*[ctypes.byref(q) for q in times]): return None
            return (times[0].dwHighDateTime<<32)|times[0].dwLowDateTime
        finally: self.k.CloseHandle(process)

    def process_parents(self):
        # 只读父子PID；不读取命令行、环境或凭据。
        snapshot=self.k.CreateToolhelp32Snapshot(2,0)
        if not snapshot or snapshot==ctypes.c_void_p(-1).value: return {}
        try:
            entry=ProcessEntry(); entry.dwSize=ctypes.sizeof(entry)
            if not self.k.Process32FirstW(snapshot,ctypes.byref(entry)): return {}
            parents={}
            for _ in range(65536):
                parents[int(entry.pid)]=int(entry.parent_pid)
                ctypes.set_last_error(0)
                if not self.k.Process32NextW(snapshot,ctypes.byref(entry)):
                    return parents if ctypes.get_last_error()==18 else {}
            return {}
        finally: self.k.CloseHandle(snapshot)

    def own_mcp_overlay(self,hwnd,cls,exstyle):
        # 官方0.8.8提示窗由截图工具在采集内部隐藏；只豁免本次MCP子树。
        pattern=r'WindowsMCP(?:Control_(?:\d+_(?:top|bottom|left|right|notice|notice_glow)|cursor)|Flash)_[0-9a-f]+'
        required=0x080800a8  # 分层、穿透、置顶、工具窗、不激活。
        if not re.fullmatch(pattern,cls) or exstyle & required != required: return False
        if self.u.GetWindow(hwnd,4) or int(self.u.GetAncestor(hwnd,2) or 0)!=int(hwnd): return False
        process=getattr(getattr(self,'mcp',None),'process',None)
        if process is None or process.poll() is not None: return False
        root=process.pid; root_created=self.process_created(root)
        pid=w.DWORD(); self.u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        parents=self.process_parents(); current=pid.value; visited=set()
        child_created=self.process_created(current); overlay_created=child_created
        if not root_created or not child_created or current==root: return False
        for _ in range(8):
            if current in visited: return False
            visited.add(current); parent=parents.get(current)
            if not parent: return False
            parent_created=self.process_created(parent)
            if not parent_created or child_created<parent_created: return False
            if parent==root:
                final_pid=w.DWORD(); self.u.GetWindowThreadProcessId(hwnd,ctypes.byref(final_pid))
                return (parent_created==root_created and process.poll() is None and final_pid.value==pid.value
                        and self.process_created(root)==root_created
                        and self.process_created(pid.value)==overlay_created)
            current=parent; child_created=parent_created
        return False

    def occlusion_visibility(self,hwnd):
        cloak=w.DWORD()
        hr=self.d.DwmGetWindowAttribute(hwnd,14,ctypes.byref(cloak),ctypes.sizeof(cloak))
        data={'handle':int(hwnd),'cloaked':int(cloak.value) if hr==0 else None,'cloak_hresult':int(hr)}
        if hr==0 and cloak.value: return {**data,'category':'cloaked'}
        exstyle=int(self.u.GetWindowLongW(hwnd,-20))&0xffffffff
        data['exstyle']=exstyle
        if exstyle & 0x80000:
            color=w.DWORD(); alpha=w.BYTE(); flags=w.DWORD()
            if self.u.GetLayeredWindowAttributes(hwnd,ctypes.byref(color),ctypes.byref(alpha),ctypes.byref(flags)):
                if flags.value & 2 and alpha.value==0: return {**data,'category':'transparent_alpha_zero'}
        cls=ctypes.create_unicode_buffer(256)
        if self.u.GetClassNameW(hwnd,cls,len(cls)) and self.own_mcp_overlay(hwnd,cls.value,exstyle):
            return {**data,'category':'own_mcp_overlay'}
        return {**data,'category':'visible_or_unknown'}

    def windows(self):
        rows=[]
        inventory={'raw_count':0,'visible_count':0,'verified_process_count':0,
                   'process_lookup_failed_count':0,'rect_failed_count':0,
                   'class_lookup_failed_count':0,'title_missing_count':0,'enum_ok':False}
        self.last_window_inventory=inventory
        def inspect(hwnd,_):
            inventory['raw_count']+=1
            if not self.u.IsWindowVisible(hwnd): return True
            inventory['visible_count']+=1
            pid=w.DWORD(); self.u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            process=self.k.OpenProcess(0x1000,False,pid.value)
            if not process:
                inventory['process_lookup_failed_count']+=1
                return True
            try:
                path=ctypes.create_unicode_buffer(32768); size=w.DWORD(len(path))
                if not self.k.QueryFullProcessImageNameW(process,0,path,ctypes.byref(size)):
                    inventory['process_lookup_failed_count']+=1
                    return True
                if not path.value.lower().endswith('\\msedge.exe'): return True
                times=[w.FILETIME() for _ in range(4)]
                if not self.k.GetProcessTimes(process,*[ctypes.byref(q) for q in times]):
                    inventory['process_lookup_failed_count']+=1
                    return True
                inventory['verified_process_count']+=1
                born=(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime
                title=ctypes.create_unicode_buffer(2048)
                self.u.GetWindowTextW(hwnd,title,len(title))
                if not title.value: inventory['title_missing_count']+=1
                rect=w.RECT()
                if not self.u.GetWindowRect(hwnd,ctypes.byref(rect)):
                    inventory['rect_failed_count']+=1
                    return True
                # 最大化窗口的非客户区可超出屏幕数像素，截图只取当前可见交集。
                vx,vy,vw,vh=[self.u.GetSystemMetrics(i) for i in (76,77,78,79)]
                visible=[max(vx,rect.left),max(vy,rect.top),min(vx+vw,rect.right),min(vy+vh,rect.bottom)]
                class_name=ctypes.create_unicode_buffer(256)
                if not self.u.GetClassNameW(hwnd,class_name,len(class_name)):
                    inventory['class_lookup_failed_count']+=1
                rows.append({'handle':int(hwnd),'pid':pid.value,'process_created':born,
                    'title':title.value,'rect':visible,
                    'window_class':class_name.value,'owner':int(self.u.GetWindow(hwnd,4) or 0),
                    'root_owner':int(self.u.GetAncestor(hwnd,3) or 0),
                    'style':int(self.u.GetWindowLongW(hwnd,-16))&0xffffffff,
                    'exstyle':int(self.u.GetWindowLongW(hwnd,-20))&0xffffffff,
                    'enabled':bool(self.u.IsWindowEnabled(hwnd)),
                    'visible':True,
                    'minimized':bool(self.u.IsIconic(hwnd))})
            finally: self.k.CloseHandle(process)
            return True
        callback=self.callback(inspect)
        inventory['enum_ok']=bool(self.u.EnumWindows(callback,0))
        return rows

    def get(self,signature):
        found=[row for row in self.windows() if all(row.get(k)==signature.get(k)
               for k in ('handle','pid','process_created'))]
        if len(found)!=1: raise RecapError('window_lost')
        return found[0]

    def selection_context(self):
        foreground=self.u.GetForegroundWindow()
        return {'foreground_handle':int(foreground or 0),
                'foreground_root':int(self.u.GetAncestor(foreground,2) or 0) if foreground else 0}

    def foreground(self,window):
        live=self.get(window)
        foreground=self.u.GetForegroundWindow()
        root=self.u.GetAncestor(foreground,2) if foreground else 0
        if int(root or 0)!=live['handle'] or live['minimized'] or not self.u.IsWindowEnabled(live['handle']):
            raise RecapError('foreground_changed')
        return live

    def ensure_switch_safe(self,window,*,allow_restore=False):
        live=self.get(window)
        if (live['minimized'] and not allow_restore) or not self.u.IsWindowEnabled(live['handle']):
            raise RecapError('foreground_changed')
        if allow_restore and (live.get('window_class')!='Chrome_WidgetWin_1'
                or live.get('owner')!=0 or live.get('root_owner')!=live['handle'] or not live.get('title')):
            raise RecapError('foreground_changed')
        foreground=self.u.GetForegroundWindow()
        root=int(self.u.GetAncestor(foreground,2) or 0) if foreground else 0
        if not root: raise RecapError('foreground_changed')
        if any(item['handle']==root and root!=live['handle'] for item in self.windows()):
            raise RecapError('foreground_changed')

    def ensure_point(self,window,point):
        live=self.foreground(window)
        if live['rect']!=window.get('rect'): raise RecapError('window_moved_before_action')
        hit=self.u.WindowFromPoint(w.POINT(int(point[0]),int(point[1])))
        root=self.u.GetAncestor(hit,2) if hit else 0
        if (not hit or int(root or 0)!=live['handle']
                or not self.u.IsWindowVisible(hit) or not self.u.IsWindowEnabled(hit)):
            raise RecapError('point_target_mismatch')
        class_name=ctypes.create_unicode_buffer(256)
        if not self.u.GetClassNameW(hit,class_name,len(class_name)) or class_name.value.casefold()=='static':
            raise RecapError('point_target_mismatch')
        current=live['handle']; visited={current}
        for _ in range(32):
            client_point=w.POINT(int(point[0]),int(point[1]))
            if not self.u.ScreenToClient(current,ctypes.byref(client_point)):
                raise RecapError('point_target_mismatch')
            child=self.u.ChildWindowFromPointEx(current,client_point,1)  # 只跳过不可见子窗，不跳过disabled。
            if not child: raise RecapError('point_target_mismatch')
            child=int(child)
            if child==current: break
            if child in visited or not self.u.IsWindowEnabled(child): raise RecapError('point_target_mismatch')
            child_class=ctypes.create_unicode_buffer(256)
            if not self.u.GetClassNameW(child,child_class,len(child_class)) or child_class.value.casefold()=='static':
                raise RecapError('point_target_mismatch')
            visited.add(child); current=child
        else:
            raise RecapError('point_target_mismatch')
        if self.foreground(window)['rect']!=window.get('rect'):
            raise RecapError('window_moved_before_action')

    def ensure_unobstructed(self,window):
        self.last_occlusion={'category':'enumeration_unknown'}
        self.occlusion_exclusions={}
        live=self.foreground(window)
        if live['rect']!=window.get('rect'): raise RecapError('window_moved_before_action')
        x,y,r,b=live['rect']; current=live['handle']; visited={current}
        for _ in range(256):
            ctypes.set_last_error(0)
            above=self.u.GetWindow(current,3)  # GW_HWNDPREV：Z序中紧邻上方。
            if not above:
                if ctypes.get_last_error():
                    self.last_occlusion={'category':'z_order_query_failed'}
                    raise RecapError('window_occluded_or_unknown')
                break
            current=int(above)
            if current in visited or not self.u.IsWindow(current):
                self.last_occlusion={'category':'z_order_cycle_or_destroyed','handle':current}
                raise RecapError('window_occluded_or_unknown')
            visited.add(current)
            if not self.u.IsWindowVisible(current) or self.u.IsIconic(current): continue
            rect=w.RECT()
            if not self.u.GetWindowRect(current,ctypes.byref(rect)):
                self.last_occlusion={'category':'rect_query_failed','handle':current}
                raise RecapError('window_occluded_or_unknown')
            if max(x,rect.left)<min(r,rect.right) and max(y,rect.top)<min(b,rect.bottom):
                visibility=self.occlusion_visibility(current)
                self.last_occlusion=visibility
                category=visibility['category']
                if category in ('cloaked','transparent_alpha_zero','own_mcp_overlay'):
                    self.occlusion_exclusions[category]=self.occlusion_exclusions.get(category,0)+1
                    continue
                raise RecapError('window_occluded_or_unknown')
        else:
            self.last_occlusion={'category':'enumeration_limit'}
            raise RecapError('window_occluded_or_unknown')
        if self.foreground(window)['rect']!=window.get('rect'):
            raise RecapError('window_moved_before_action')

    def foreground_info(self):
        hwnd=self.u.GetForegroundWindow()
        title=ctypes.create_unicode_buffer(2048)
        self.u.GetWindowTextW(hwnd,title,len(title))
        pid=w.DWORD(); self.u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        return {'handle':int(hwnd or 0),'pid':pid.value,'title':title.value}

    def contained(self,region):
        x,y,r,b=region
        # 虚拟屏幕边界来自系统，只读；不扩大截图去覆盖无关窗口。
        get=self.u.GetSystemMetrics
        vx,vy,vw,vh=[get(i) for i in (76,77,78,79)]
        return vx<=x<r<=vx+vw and vy<=y<b<=vy+vh
