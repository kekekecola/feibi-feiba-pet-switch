"""A bounded subprocess boundary around potentially blocking COM providers."""
import subprocess
import json
import queue
import threading
import uuid


class WorkerError(RuntimeError):
    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details


class WarmWorker:
    """Reuse COM initialization, never cache pet controls; kill on uncertainty."""
    def __init__(self, command):
        self.command = command
        self.process = None

    def close(self):
        process, self.process = self.process, None
        if process:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            for stream in (process.stdin, process.stdout):
                stream.close()

    def start(self):
        if self.process and self.process.poll() is None:
            return
        self.close()
        self.messages = queue.Queue()
        process = self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding='utf-8', bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW)
        messages = self.messages
        def read():
            try:
                for line in process.stdout:
                    try:
                        messages.put(json.loads(line))
                    except ValueError:
                        messages.put({'transport_error':'工作进程返回了无效数据'})
            finally:
                messages.put({'transport_error':'工作进程已退出'})
        threading.Thread(target=read, daemon=True).start()
        message = self.messages.get(timeout=8)
        if message != {'ready':True}:
            raise WorkerError('工作进程初始化失败')

    def run(self, action, progress_token, timeout):
        try:
            self.start()
            job = uuid.uuid4().hex
            self.process.stdin.write(json.dumps(dict(job=job, action=action, token=progress_token))+'\n')
            self.process.stdin.flush()
            message = self.messages.get(timeout=timeout)
            if message.get('job') != job:
                raise WorkerError(message.get('transport_error','工作进程回执不匹配'))
            if message.get('error'):
                raise WorkerError(message['error'], message.get('control_details'))
            return message['result']
        except queue.Empty:
            self.close()
            raise TimeoutError('原生控件调用超时，已结束预热工作进程。') from None
        except Exception:
            self.close()
            raise


def run(command, timeout=18):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            # A provider-launched descendant must not keep our pipe wait alive.
            process.stdout.close()
            process.stderr.close()
            process.wait(timeout=2)
        raise TimeoutError('原生控件调用超时，已中断隔离进程。') from None
    if process.returncode:
        message = (stdout or stderr).decode('utf-8', 'replace')[-2000:]
        try:
            data = json.loads(message)
        except ValueError:
            raise WorkerError(message) from None
        raise WorkerError(data.get('error',message), data.get('control_details'))
    return stdout.decode('utf-8')
