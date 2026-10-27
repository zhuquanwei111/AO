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
            (self.u.IsWindowVisible,[w.HWND],w.BOOL),(self.u.IsIconic,[w.HWND],w.BOOL),
            (self.u.GetForegroundWindow,[],w.HWND),(self.u.GetWindowRect,[w.HWND,ctypes.POINTER(w.RECT)],w.BOOL),
            (self.u.GetWindowTextW,[w.HWND,w.LPWSTR,ctypes.c_int],ctypes.c_int),
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
                rows.append({'handle':int(hwnd),'pid':pid.value,'process_created':born,
                    'title':title.value,'rect':visible,
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
        if int(self.u.GetForegroundWindow() or 0)!=live['handle'] or live['minimized']:
            raise RecapError('foreground_changed')
        return live

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
