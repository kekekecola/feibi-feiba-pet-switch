"""Do not endlessly fight a client which keeps showing its overlay."""
def run(user, hwnd, stop, conflict, withdraw_cg):
    reappearances = 0
    while not stop.wait(.05):
        if not user.IsWindow(hwnd):
            return
        if user.IsWindowVisible(hwnd):
            reappearances += 1
            if reappearances > 2:
                conflict.set()
                withdraw_cg()
                return
            user.ShowWindow(hwnd, 0)
