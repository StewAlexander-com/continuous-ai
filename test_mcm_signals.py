"""MCM signal handlers are opt-in (library-safe default)."""
import signal
import sys

sys.path.insert(0, ".")


def test_default_does_not_install_handlers():
    import mcm as M
    # Capture handlers before construction
    prev_int = signal.getsignal(signal.SIGINT)
    prev_term = signal.getsignal(signal.SIGTERM)
    # Patch storage.init_db so we don't touch the real DB
    real_init = M.storage.init_db
    M.storage.init_db = lambda: None
    try:
        m = M.MCM()  # default: install_signal_handlers=False
        assert signal.getsignal(signal.SIGINT) is prev_int
        assert signal.getsignal(signal.SIGTERM) is prev_term
        # Opt-in installs
        m.install_signal_handlers()
        assert signal.getsignal(signal.SIGINT) is not prev_int
        assert signal.getsignal(signal.SIGTERM) is not prev_term
        # Restore so we don't leave the process weird
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)
        print("[PASS] MCM default leaves host signal handlers alone")
    finally:
        M.storage.init_db = real_init
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)


def test_opt_in_kwarg_installs():
    import mcm as M
    prev_int = signal.getsignal(signal.SIGINT)
    prev_term = signal.getsignal(signal.SIGTERM)
    real_init = M.storage.init_db
    M.storage.init_db = lambda: None
    try:
        m = M.MCM(install_signal_handlers=True)
        assert signal.getsignal(signal.SIGINT) == m._handle_signal
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)
        print("[PASS] install_signal_handlers=True registers handlers")
    finally:
        M.storage.init_db = real_init
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)


if __name__ == "__main__":
    test_default_does_not_install_handlers()
    test_opt_in_kwarg_installs()
    print("\nALL MCM SIGNAL TESTS PASSED")
    sys.exit(0)
