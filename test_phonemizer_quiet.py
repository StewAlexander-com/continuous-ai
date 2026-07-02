"""The cosmetic phonemizer 'words count mismatch' warning must never reach the
console — even after phonemizer re-initializes its own logger (which resets the
level to WARNING and is what defeated the naive level-only suppression).

CI-safe: does NOT import the phonemizer package. It simulates exactly what
phonemizer.logger.get_logger() does to the 'phonemizer' logger, then asserts the
warning does not propagate to a root handler.

Run: ./.venv/bin/python test_phonemizer_quiet.py
"""
import logging
import seedling as S


_passed = 0
_failed = 0


def check(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  PASS  {msg}")
    else:
        _failed += 1
        print(f"  FAIL  {msg}")


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _simulate_phonemizer_reinit():
    """Mirror phonemizer.logger.get_logger(): clear handlers, reset level to
    WARNING, add its own handler. Crucially it does NOT touch `propagate`."""
    lg = logging.getLogger("phonemizer")
    lg.handlers = []
    lg.setLevel(logging.WARNING)
    lg.addHandler(logging.NullHandler())
    return lg


def test_setup_disables_propagation():
    print("\ntest_setup_disables_propagation")
    S._setup_logging("INFO")
    check(logging.getLogger("phonemizer").propagate is False,
          "phonemizer logger has propagate=False after setup")
    check(logging.getLogger("espeak").propagate is False,
          "espeak logger has propagate=False after setup")


def test_warning_does_not_reach_root_after_reinit():
    print("\ntest_warning_does_not_reach_root_after_reinit")
    S._setup_logging("INFO")
    cap = _Capture()
    logging.getLogger().addHandler(cap)          # a root handler, like our console
    try:
        lg = _simulate_phonemizer_reinit()        # library clobbers level back to WARNING
        check(lg.level == logging.WARNING, "sanity: reinit reset level to WARNING (defeats level-only fix)")
        lg.warning("words count mismatch on 100.0%% of the lines (1/1)")
        leaked = [r for r in cap.records if (r.name or "").startswith("phonemizer")]
        check(not leaked, "count-mismatch warning does NOT reach a root handler")
    finally:
        logging.getLogger().removeHandler(cap)


def test_console_filter_drops_mismatch_but_keeps_real_errors():
    print("\ntest_console_filter_drops_mismatch_but_keeps_real_errors")
    f = S._PhonemizerNoiseFilter()

    def rec(name, level, msg):
        return logging.LogRecord(name, level, __file__, 0, msg, None, None)

    check(f.filter(rec("phonemizer", logging.WARNING,
                       "words count mismatch on 100.0% of the lines (1/1)")) is False,
          "drops the cosmetic count-mismatch WARNING")
    check(f.filter(rec("phonemizer", logging.ERROR, "espeak not installed")) is True,
          "keeps a genuine phonemizer ERROR")
    check(f.filter(rec("session", logging.WARNING, "words count mismatch")) is True,
          "never touches non-phonemizer loggers")


if __name__ == "__main__":
    for fn in (
        test_setup_disables_propagation,
        test_warning_does_not_reach_root_after_reinit,
        test_console_filter_drops_mismatch_but_keeps_real_errors,
    ):
        fn()
    print("\n" + "=" * 50)
    print(f"{_passed} passed, {_failed} failed")
    print("=" * 50)
    raise SystemExit(1 if _failed else 0)
