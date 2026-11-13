"""Local themed control room. Closing this window never quits the bridge."""
from pathlib import Path
import argparse
import ctypes
from ctypes import wintypes
import json
import os
import sys
import time
import threading
import subprocess
import uuid
import hashlib
import logging
import tkinter as tk
from tkinter import font as tkfont
from PIL import Image, ImageTk, ImageGrab

HERE = Path(__file__).resolve().parent
from paths import RUNTIME as DATA, VERSION
import settings
import diagnostics
W, H = 1040, 700
C = dict(ink='#45405E', muted='#746B84', accent='#7865AE', light='#EDE6FA', border='#E9E1F2', cream='#FFFCF7', green='#5C9B85')


def read(name):
    try:
        return json.loads((DATA / name).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


class ControlRoom:
    def __init__(self, page=None):
        gdi=ctypes.WinDLL('gdi32')
        gdi.AddFontResourceExW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint,ctypes.c_void_p]
        gdi.AddFontResourceExW('C:/Windows/Fonts/SIMYOU.TTF',0x10,None)
        self.root = tk.Tk()
        self.root.title('菲比的小小控制室')
        self.root.withdraw()
        self.root.overrideredirect(True)
        work=wintypes.RECT()
        user=ctypes.WinDLL('user32');user.SystemParametersInfoW(0x30,0,ctypes.byref(work),0)
        self.scale=min(1.0,(work.right-work.left-32)/W,(work.bottom-work.top-32)/H)
        self.pixel_w,self.pixel_h=round(W*self.scale),round(H*self.scale)
        self.root.geometry(f'{self.pixel_w}x{self.pixel_h}+{work.left+(work.right-work.left-self.pixel_w)//2}+{work.top+(work.bottom-work.top-self.pixel_h)//2}')
        self.root.iconbitmap(str(HERE/'assets'/'feibi-tray.ico'))
        self.root.update_idletasks()
        try:
            user.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT];user.GetAncestor.restype=wintypes.HWND
            corner=ctypes.c_int(2)
            dwm=ctypes.WinDLL('dwmapi');dwm.DwmSetWindowAttribute.argtypes=[wintypes.HWND,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD]
            dwm.DwmSetWindowAttribute(user.GetAncestor(self.root.winfo_id(),2),33,ctypes.byref(corner),ctypes.sizeof(corner))
        except OSError:
            pass
        families = tkfont.families(self.root)
        self.family = next((f for f in ('幼圆','YouYuan') if f in families),'Microsoft YaHei UI')
        self.c = tk.Canvas(self.root,width=self.pixel_w,height=self.pixel_h,highlightthickness=0,bg=C['cream'])
        self.c.pack()
        with Image.open(HERE/'ui_assets'/'control-room-background.png') as image:
            self.bg = ImageTk.PhotoImage(image.resize((self.pixel_w,self.pixel_h),Image.Resampling.LANCZOS))
        with Image.open(HERE/'assets'/'feibi-tray.ico') as image:
            self.avatar = ImageTk.PhotoImage(image.resize((round(56*self.scale),round(56*self.scale)),Image.Resampling.LANCZOS))
        self.tab = {'hotkeys':0,'status':1,'diagnostics':2}.get(page,0)
        self.detail = False
        self.busy = False
        self.closed = False
        self.dismissed = False
        self.last_request = read('control-room-request.json').get('nonce')
        self.last_snapshot = None
        self.children = []
        self.drag = None
        state=read('bridge-status.json')
        self.bridge_pid=state.get('pid') if state.get('running') else None
        self.feedback = '保存后约 1 秒生效，冲突时保留当前按键。'
        self.entry_value = read('bridge-status.json').get('hotkey','F8')
        self.c.bind('<ButtonPress-1>',self.drag_start)
        self.c.bind('<B1-Motion>',self.drag_move)
        self.root.bind('<Escape>',lambda _:self.hide())
        self.root.protocol('WM_DELETE_WINDOW',self.hide)
        self.draw()
        self.root.report_callback_exception=self.callback_error
        self.root.after(200,self.tick)
        self.root.after(100,lambda:self.show() if not self.dismissed else None)

    def round(self,x,y,w,h,r=18,fill='#FFFFFF',outline='',width=1):
        points=[x+r,y,x+w-r,y,x+w,y,x+w,y+r,x+w,y+h-r,x+w,y+h,x+w-r,y+h,x+r,y+h,x,y+h,x,y+h-r,x,y+r,x,y]
        if not fill:
            return self.c.create_line(points+points[:4],smooth=True,splinesteps=24,fill=outline,width=width)
        return self.c.create_polygon(points,smooth=True,splinesteps=24,fill=fill,outline=outline,width=width)

    def text(self,x,y,value,size=20,color=None,bold=False,anchor='nw',width=None,latin=False):
        return self.c.create_text(x,y,text=value,font=('Segoe UI' if latin else self.family,-round(size*self.scale),'bold' if bold else 'normal'),
                                  fill=color or C['ink'],anchor=anchor,width=round(width*self.scale) if width else None)

    def button(self,x,y,w,h,label,callback,primary=False,small=False):
        fill,fg = (C['accent'],'#FFFFFF') if primary else ('#FAF7FF',C['accent'])
        rect=self.round(x,y,w,h,14,fill,C['accent'] if primary else '#DFD4EF')
        label_id=self.text(x+w/2,y+h/2,label,17 if small else 20,fg,anchor='center',bold=primary)
        for item in (rect,label_id):
            self.c.tag_bind(item,'<Button-1>',lambda e:callback())
            self.c.tag_bind(item,'<Enter>',lambda e:self.c.configure(cursor='hand2'))
            self.c.tag_bind(item,'<Leave>',lambda e:self.c.configure(cursor=''))

    def card(self,x,y,w,h):
        self.round(x+2,y+5,w,h,24,'#E6DFEB')
        self.round(x,y,w,h,24,'#FFFEFC','#EEE7F2')

    def drag_start(self,event):
        if event.y<72*self.scale:
            self.drag=(event.x_root-self.root.winfo_x(),event.y_root-self.root.winfo_y())
        else:
            self.drag=None

    def drag_move(self,event):
        if self.drag:
            self.root.geometry(f'+{event.x_root-self.drag[0]}+{event.y_root-self.drag[1]}')

    def change_tab(self,index):
        if self.tab==0 and hasattr(self,'entry') and self.entry.winfo_exists():
            self.entry_value=self.entry.get()
        self.tab=index
        self.detail=False
        self.draw()

    def draw(self):
        for widget in self.children:
            widget.destroy()
        self.children=[]
        self.c.delete('all')
        self.c.create_image(0,0,image=self.bg,anchor='nw')
        self.round(18,18,180,H-36,26,'#FFFCF7','#E9E2EF')
        self.c.create_image(48,43,image=self.avatar,anchor='nw')
        self.text(40,116,'菲比的小小',23,bold=True)
        self.text(40,145,'控制室',30,C['accent'],bold=True)
        self.text(41,188,'FEIBI COMPANION',11,C['muted'],latin=True)
        for index,label in enumerate(['快捷键设置','伙伴状态','更新与诊断']):
            y=257+index*65
            selected=index==self.tab
            area=self.round(30,y,155,47,15,C['light'] if selected else '#FFFCF7')
            icon=self.text(48,y+23,'✦' if selected else '·',20,C['accent'],anchor='center',latin=True)
            word=self.text(65,y+23,label,19,C['accent'] if selected else '#746B84',anchor='w',bold=selected)
            for item in (area,icon,word):
                self.c.tag_bind(item,'<Button-1>',lambda e,i=index:self.change_tab(i))
                self.c.tag_bind(item,'<Enter>',lambda e:self.c.configure(cursor='hand2'))
                self.c.tag_bind(item,'<Leave>',lambda e:self.c.configure(cursor=''))
        self.text(44,591,'小小按键',18,C['muted'])
        self.text(44,621,'大大陪伴',18,C['accent'])
        self.text(232,39,'菲比啾比  /  菲八啾比',16,C['muted'])
        close_area=self.round(954,18,48,48,14,'#F5EFFA')
        close_a=self.c.create_line(971,35,985,49,fill='#77658F',width=2.5,capstyle='round')
        close_b=self.c.create_line(971,49,985,35,fill='#77658F',width=2.5,capstyle='round')
        for item in (close_area,close_a,close_b):
            self.c.tag_bind(item,'<Button-1>',lambda _:self.hide())
            self.c.tag_bind(item,'<Enter>',lambda _:self.c.configure(cursor='hand2'))
            self.c.tag_bind(item,'<Leave>',lambda _:self.c.configure(cursor=''))
        state=read('bridge-status.json')
        key=state.get('hotkey','F8')
        self.round(788,28,158,33,16,'#F5F8F2','#E4ECDD')
        self.text(867,44,'●  '+key+' 已就绪' if state.get('hotkey_registered') else '●  等待连接',14,C['green'],anchor='center')
        self.text(234,655,'菲比的陪伴控制室  ·  关闭窗口后快捷键仍可用',14,C['muted'])
        if self.tab==0:
            self.hotkey_page()
        elif self.tab==1:
            self.status_page()
        else:
            self.detail_page() if self.detail else self.update_page()
        self.round(2,2,W-4,H-4,22,'','#A391C2',width=3)
        self.round(6,6,W-12,H-12,19,'','#FFFFFF',width=1)
        self.c.create_line(218,75,1007,75,fill='#E6DCEF')
        self.c.scale('all',0,0,self.scale,self.scale)

    def heading(self,title,subtitle):
        self.text(236,102,title,32,bold=True)
        self.text(236,151,subtitle,18,C['muted'])

    def choose(self,key):
        self.entry.delete(0,'end');self.entry.insert(0,key)

    def save(self):
        if self.busy:
            return
        self.entry_value=self.entry.get().strip()
        try:
            requested=settings.save_hotkey(self.entry_value)
        except (ValueError,OSError) as error:
            self.feedback=str(error)[:100]
            self.draw()
            return
        self.entry_value=requested
        self.feedback='已保存 '+requested+'，正在等待后台确认…'
        self.draw()
        self.root.after(1700,lambda:self.confirm_key(requested))

    def confirm_key(self,requested):
        state=read('bridge-status.json')
        if not state.get('running') or time.time()-state.get('updated_at',0)>6:
            message='配置已保存；后台未运行，启动后生效。'
        elif state.get('hotkey_error'):
            message=state['hotkey_error']
        elif state.get('hotkey')==requested and state.get('hotkey_registered'):
            message='已生效：'+requested+'。现在就可以试试换装啦。'
        else:
            message='后台尚未确认，请查看状态与诊断。'
        self.feedback=message[:100]
        if self.tab==0:
            self.draw()

    def hotkey_page(self):
        self.heading('给换装一个小暗号','轻轻一按，在戴帽与摘帽之间切换。')
        self.card(232,204,426,346)
        self.text(255,230,'切换快捷键',22,bold=True)
        self.text(255,267,'功能键，或 Ctrl / Alt / Shift 组合',15,C['muted'])
        self.round(254,304,380,63,16,'#F7F2FF','#DCD0EF')
        self.entry=tk.Entry(self.root,font=('Segoe UI',-round(26*self.scale),'bold'),fg='#6F5C9F',bg='#F7F2FF',insertbackground=C['accent'],
                            borderwidth=0,highlightthickness=0,relief='flat')
        self.entry.insert(0,self.entry_value)
        self.c.create_window(274,316,window=self.entry,width=round(335*self.scale),height=round(38*self.scale),anchor='nw')
        self.children.append(self.entry)
        self.text(255,389,'常用小暗号',15,C['muted'])
        self.button(255,418,98,38,'F8 默认',lambda:self.choose('F8'),small=True)
        self.button(365,418,157,38,'Ctrl + Alt + H',lambda:self.choose('Ctrl+Alt+H'),small=True)
        self.button(255,484,127,43,'恢复默认',lambda:self.choose('F8'))
        self.button(395,484,239,43,'保存快捷键  ✦',self.save,primary=True)
        self.round(233,568,425,68,15,'#F5F1FA','#E9E0F1')
        self.text(252,582,self.feedback,15,'#746B84',width=390)

    def status_page(self):
        self.heading('陪伴，正在进行中','把小小的运行状态，整理得一目了然。')
        state=read('bridge-status.json')
        ready=state.get('connection_state')=='ready'
        self.card(232,204,426,112)
        self.round(255,232,45,45,18,'#EDF5EF')
        self.text(278,255,'✓',25,C['green'],anchor='center',latin=True)
        self.text(318,230,'伙伴已就位' if ready else '伙伴连接中',25,bold=True)
        self.text(318,271,'原生宠物  ·  自适应大小',15,C['muted'])
        self.card(232,338,426,220)
        names={'feibi':'菲比啾比 · 戴帽','feiba':'菲八啾比 · 摘帽'}
        rows=[('当前形态',names.get(state.get('form'),'等待识别')),('切换快捷键',state.get('hotkey','F8')),
              ('后台服务','运行中' if state.get('running') else '未运行'),('程序版本','v'+state.get('version','1.5.1'))]
        for i,(label,value) in enumerate(rows):
            y=365+i*48
            self.text(255,y,label,18,C['muted']);self.text(632,y,value,19,anchor='ne')
            if i<3:self.c.create_line(255,y+34,633,y+34,fill='#F0EAF5')
        self.button(232,582,170,43,'刷新状态',self.draw)
        target='put-on' if state.get('form')=='feiba' else 'take-off'
        self.button(416,582,242,43,'切换中…' if self.busy else ('戴帽 · 菲比' if target=='put-on' else '摘帽 · 菲八'),lambda:self.switch(target),primary=True)

    def update_page(self):
        self.heading('让陪伴顺顺利利','更新与诊断，都在这里安心查看。')
        state=read('update-status.json')
        self.card(232,204,426,159)
        self.text(255,231,'版本更新',23,bold=True)
        self.text(255,270,'已安装  v'+state.get('installed_version','1.5.1'),19)
        self.text(255,307,'本机外观试用 · 自动覆盖已暂停' if read('local-theme-review.json').get('active') else '登录时及每 30 分钟检查稳定版',15,C['muted'])
        self.button(494,260,140,42,'检查更新',self.check_update,small=True)
        self.card(232,385,426,171)
        self.text(255,410,'状态与诊断',23,bold=True)
        self.text(255,451,'遇到小状况，先看看运行状态。',17,C['muted'])
        self.text(255,486,'报告保存在本机，不会自动上传。',15,C['muted'])
        self.button(490,504,144,35,'查看诊断详情',self.open_detail,small=True)
        self.button(232,578,202,40,'打开日志文件夹',lambda:os.startfile(str(DATA)),small=True)
        self.button(448,578,210,40,'使用说明',self.help,small=True)
        self.text(241,628,'诊断只保存在本机，不会自动上传',14,C['muted'])

    def notice(self,text):
        self.round(235,610,422,33,12,'#EEE5F9')
        self.text(446,627,text,16,C['accent'],anchor='center')

    def async_call(self,command,done):
        if self.busy:
            return
        self.busy=True
        def work():
            try:
                result=subprocess.run(command,capture_output=True,encoding='utf-8',timeout=40,creationflags=subprocess.CREATE_NO_WINDOW)
                error=None
            except Exception as exception:
                result,error=None,str(exception)
            def finish():
                self.busy=False
                done(result,error)
            try:
                self.root.after(0,finish)
            except (tk.TclError,RuntimeError):
                pass
        threading.Thread(target=work,daemon=True).start()

    def switch(self,target):
        def done(result,error):
            if error or result.returncode:
                self.feedback=error or '切换未完成，请查看诊断详情。'
            self.draw()
        self.async_call([str(Path(sys.executable).with_name('python.exe')),str(HERE/'bridge_control.py'),target],done)
        self.draw()

    def open_detail(self):
        self.detail=True
        self.draw()

    def detail_page(self):
        from tkinter.scrolledtext import ScrolledText
        self.heading('把小状况看清楚','只在本机生成，不会自动上传诊断。')
        self.card(232,204,426,385)
        report=diagnostics.collect()
        self.report=json.dumps(report,ensure_ascii=False,indent=2)
        box=ScrolledText(self.root,wrap='word',font=('Microsoft YaHei UI',-round(14*self.scale)),
                         bg='#FFFEFC',fg=C['ink'],relief='flat',highlightthickness=0)
        box.insert('1.0',self.report);box.configure(state='disabled')
        self.c.create_window(250,222,window=box,width=round(390*self.scale),height=round(344*self.scale),anchor='nw')
        self.children.append(box)
        self.button(232,606,130,40,'返回',self.close_detail,small=True)
        self.button(374,606,130,40,'刷新报告',self.draw,small=True)
        self.button(516,606,142,40,'复制诊断',self.copy_report,primary=True,small=True)

    def close_detail(self):
        self.detail=False
        self.draw()

    def copy_report(self):
        self.root.clipboard_clear();self.root.clipboard_append(self.report)

    def help(self):
        import webbrowser
        webbrowser.open('https://github.com/kekekecola/feibi-feiba-pet-switch#readme')

    def check_update(self):
        if not os.environ.get('FEIBI_SWITCH_HOME') and not (DATA.parent/'current.json').exists():
            self.notice('自动更新未开启，请点使用说明查看开启方法。')
            return
        if read('local-theme-review.json').get('active'):
            self.notice('本机外观试用中，暂不自动覆盖这版界面。')
            return
        def done(result,error):
            self.notice('更新任务已启动，稍后刷新状态。' if not error and result.returncode==0 else '未能启动更新，请查看诊断。')
        self.async_call(['schtasks','/Run','/TN','FeibiFeibaPetSwitchUpdate'],done)

    def callback_error(self,kind,error,traceback):
        logging.error('Control room callback failed',exc_info=(kind,error,traceback))

    def hide(self):
        self.dismissed=True
        self.root.withdraw()
        self.heartbeat()

    def show(self):
        self.dismissed=False
        self.root.deiconify()
        self.root.update_idletasks()
        user=ctypes.WinDLL('user32')
        user.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT];user.GetAncestor.restype=wintypes.HWND
        user.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int]
        user.SetForegroundWindow.argtypes=[wintypes.HWND]
        user.SetWindowPos.argtypes=[wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT]
        hwnd=user.GetAncestor(self.root.winfo_id(),2)
        get=user.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p)==8 else user.GetWindowLongW
        put=user.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p)==8 else user.SetWindowLongW
        get.argtypes=[wintypes.HWND,ctypes.c_int];get.restype=ctypes.c_ssize_t
        put.argtypes=[wintypes.HWND,ctypes.c_int,ctypes.c_ssize_t];put.restype=ctypes.c_ssize_t
        put(hwnd,-20,(get(hwnd,-20)&~0x80)|0x40000)
        user.ShowWindow(hwnd,9)
        user.SetWindowPos(hwnd,wintypes.HWND(-1),0,0,0,0,0x53)
        user.SetWindowPos(hwnd,wintypes.HWND(-2),0,0,0,0,0x53)
        user.SetForegroundWindow(hwnd)
        self.root.lift();self.root.focus_force()

    def heartbeat(self):
        user=ctypes.WinDLL('user32')
        user.GetAncestor.argtypes=[wintypes.HWND,wintypes.UINT];user.GetAncestor.restype=wintypes.HWND
        user.IsWindowVisible.argtypes=[wintypes.HWND]
        hwnd=user.GetAncestor(self.root.winfo_id(),2)
        target=DATA/'control-room-heartbeat.json';temp=target.with_suffix('.tmp')
        temp.write_text(json.dumps(dict(pid=os.getpid(),hwnd=hwnd,visible=bool(user.IsWindowVisible(hwnd)),nonce=self.last_request,closed_by_user=self.dismissed,updated_at=time.time())),encoding='utf-8')
        os.replace(temp,target)

    def tick(self):
        try:
            self.tick_once()
            self.heartbeat()
        except Exception:
            logging.exception('Control room wake/refresh failed; polling retained')
        finally:
            if not self.closed:
                try:self.root.after(250,self.tick)
                except tk.TclError:pass

    def tick_once(self):
        state=read('bridge-status.json')
        if self.bridge_pid and ((state.get('pid') and state['pid']!=self.bridge_pid) or (state.get('running') is False and state.get('phase')=='stopped')):
            self.closed=True
            self.root.destroy()
            return
        if state.get('running'):
            self.bridge_pid=state.get('pid')
        request=read('control-room-request.json')
        if request.get('nonce') and request['nonce']!=self.last_request:
            self.last_request=request['nonce']
            page=request.get('page')
            if page:
                self.tab={'hotkeys':0,'status':1,'diagnostics':2}.get(page,0)
                self.detail=False
                self.draw()
            self.show()
        if self.root.state()!='withdrawn' and self.tab!=0 and not self.detail and not self.busy:
            state=read('bridge-status.json');updater=read('update-status.json')
            snapshot=tuple(state.get(k) for k in ('phase','form','hotkey','version','error'))+tuple(updater.get(k) for k in ('phase','installed_version','latest_version','error'))
            if snapshot!=self.last_snapshot:
                self.last_snapshot=snapshot
                self.draw()

    def capture_all(self):
        def capture(index):
            self.change_tab(index)
            self.root.update()
            x,y=self.root.winfo_rootx(),self.root.winfo_rooty()
            ImageGrab.grab(bbox=(x,y,x+self.pixel_w,y+self.pixel_h),all_screens=True).save(DATA/f'control-room-preview-{index+1}.png')
            if index<2:self.root.after(350,lambda:capture(index+1))
            else:self.root.after(100,self.root.destroy)
        self.root.after(600,lambda:capture(0))


def send_open(page=None):
    DATA.mkdir(parents=True,exist_ok=True)
    target=DATA/'control-room-request.json'
    temporary=target.with_suffix('.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(json.dumps({'nonce':uuid.uuid4().hex,'page':page}),encoding='utf-8')
    os.replace(temporary,target)


def run(page=None,capture=False):
    user=ctypes.WinDLL('user32');user.SetProcessDpiAwarenessContext.argtypes=[ctypes.c_void_p];user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR];kernel.CreateMutexW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    name='FeibiControlRoom_'+hashlib.sha256(str(DATA.resolve()).casefold().encode()).hexdigest()[:24]
    ctypes.set_last_error(0)
    handle=kernel.CreateMutexW(None,False,name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error()==183:
        kernel.CloseHandle(handle);send_open(page);return
    try:
        room=ControlRoom(page)
        if capture:
            room.capture_all()
        room.root.mainloop()
    finally:
        kernel.CloseHandle(handle)


if __name__=='__main__':
    import logging
    from logging.handlers import RotatingFileHandler
    DATA.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(handlers=[RotatingFileHandler(DATA/'control-room.log',maxBytes=512000,backupCount=1,encoding='utf-8')],level=logging.INFO)
    parser=argparse.ArgumentParser();parser.add_argument('--capture-all',action='store_true');parser.add_argument('--page',choices=('hotkeys','status','diagnostics'));args=parser.parse_args()
    try:
        run(args.page,args.capture_all)
    except Exception:
        logging.exception('Control room failed')
        raise
