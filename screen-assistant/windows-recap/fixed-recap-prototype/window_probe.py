"""只读窗口元数据：唯一窗口、前台和屏幕边界；所有输入仍经Windows-MCP。"""
import ctypes
from ctypes import wintypes as w
from core import RecapError


class Probe:
    def __init__(self):
        self.u=ctypes.WinDLL('user32',use_last_error=True)
        self.k=ctypes.WinDLL('kernel32',use_last_error=True)
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
            (self.u.GetWindowThreadProcessId,[w.HWND,ctypes.POINTER(w.DWORD)],w.DWORD),
            (self.k.OpenProcess,[w.DWORD,w.BOOL,w.DWORD],w.HANDLE),
            (self.k.QueryFullProcessImageNameW,[w.HANDLE,w.DWORD,w.LPWSTR,ctypes.POINTER(w.DWORD)],w.BOOL),
            (self.k.GetProcessTimes,[w.HANDLE]+[ctypes.POINTER(w.FILETIME)]*4,w.BOOL),
            (self.k.CloseHandle,[w.HANDLE],w.BOOL)]
        for fn,args,result in signatures: fn.argtypes=args; fn.restype=result
        # 仅设置本原型进程DPI坐标模式，避免读取逻辑像素后传给物理输入。
        dpi=self.u.SetProcessDpiAwarenessContext
        dpi.argtypes=[w.HANDLE]; dpi.restype=w.BOOL
        dpi(w.HANDLE(-4))

    def windows(self):
        rows=[]
        def inspect(hwnd,_):
            if not self.u.IsWindowVisible(hwnd): return True
            title=ctypes.create_unicode_buffer(2048)
            self.u.GetWindowTextW(hwnd,title,len(title))
            pid=w.DWORD(); self.u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            process=self.k.OpenProcess(0x1000,False,pid.value)
            if not process: return True
            try:
                path=ctypes.create_unicode_buffer(32768); size=w.DWORD(len(path))
                if not self.k.QueryFullProcessImageNameW(process,0,path,ctypes.byref(size)): return True
                if not path.value.lower().endswith('\\msedge.exe'): return True
                times=[w.FILETIME() for _ in range(4)]
                if not self.k.GetProcessTimes(process,*[ctypes.byref(q) for q in times]): return True
                born=(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime
                rect=w.RECT()
                if not self.u.GetWindowRect(hwnd,ctypes.byref(rect)): return True
                # 最大化窗口的非客户区可超出屏幕数像素，截图只取当前可见交集。
                vx,vy,vw,vh=[self.u.GetSystemMetrics(i) for i in (76,77,78,79)]
                visible=[max(vx,rect.left),max(vy,rect.top),min(vx+vw,rect.right),min(vy+vh,rect.bottom)]
                class_name=ctypes.create_unicode_buffer(256)
                self.u.GetClassNameW(hwnd,class_name,len(class_name))
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
        self.u.EnumWindows(callback,0)
        return rows

    def get(self,signature):
        found=[row for row in self.windows() if all(row.get(k)==signature.get(k)
               for k in ('handle','pid','process_created'))]
        if len(found)!=1: raise RecapError('window_lost')
        return found[0]

    def foreground(self,window):
        live=self.get(window)
        foreground=self.u.GetForegroundWindow()
        root=self.u.GetAncestor(foreground,2) if foreground else 0
        if int(root or 0)!=live['handle'] or live['minimized'] or not self.u.IsWindowEnabled(live['handle']):
            raise RecapError('foreground_changed')
        return live

    def ensure_switch_safe(self,window):
        live=self.get(window)
        if live['minimized'] or not self.u.IsWindowEnabled(live['handle']):
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
        live=self.foreground(window)
        if live['rect']!=window.get('rect'): raise RecapError('window_moved_before_action')
        x,y,r,b=live['rect']; current=live['handle']; visited={current}
        for _ in range(256):
            ctypes.set_last_error(0)
            above=self.u.GetWindow(current,3)  # GW_HWNDPREV：Z序中紧邻上方。
            if not above:
                if ctypes.get_last_error(): raise RecapError('window_occluded_or_unknown')
                break
            current=int(above)
            if current in visited or not self.u.IsWindow(current):
                raise RecapError('window_occluded_or_unknown')
            visited.add(current)
            if not self.u.IsWindowVisible(current) or self.u.IsIconic(current): continue
            rect=w.RECT()
            if not self.u.GetWindowRect(current,ctypes.byref(rect)):
                raise RecapError('window_occluded_or_unknown')
            if max(x,rect.left)<min(r,rect.right) and max(y,rect.top)<min(b,rect.bottom):
                raise RecapError('window_occluded_or_unknown')
        else:
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
