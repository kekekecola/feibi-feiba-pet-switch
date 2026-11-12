"""A bounded subprocess boundary around potentially blocking COM providers."""
import subprocess


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
        raise RuntimeError((stdout or stderr).decode('utf-8', 'replace')[-800:])
    return stdout.decode('utf-8')
