"""Withdraw owned CG before restoring an interrupted native window."""
import native_bridge
import recovery_journal

if __name__ == '__main__':
    native_bridge.hide_cg()
    if not recovery_journal.recover(native_bridge.cg_visible()):
        raise SystemExit(1)
