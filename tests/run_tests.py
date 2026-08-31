"""Run the pytest suite in a subprocess with a hard wall-clock timeout.

Every test file except test_packaging.py loads main.py through the `spg`
fixture, which creates the MediaPipe Tasks API FaceDetector at import time.
Doing that inside a pytest process reliably hangs pytest's own shutdown --
reproduced with a single trivial test, with capture on and off, with the
anyio plugin disabled, and via two independent process-launch mechanisms
(a plain subprocess and PowerShell's Start-Process). Every test's PASS/FAIL
line prints normally; the hang happens afterwards, before the session
summary, so pytest.main() itself never returns. This is not a flaky timing
issue -- CPU usage during the hang is ~0%, i.e. something is blocked, not
slow. Root cause looks like an interaction between pytest's session teardown
and the native (non-Python) thread pool MediaPipe's XNNPACK delegate spawns,
but that is a downstream library's problem, not this app's.

The workaround: run pytest as a disposable child process. If it exits on its
own, trust its real exit code -- that is the common path for test_packaging.py
and for a future test file that never imports main.py. If it does not exit
within TIMEOUT_SECONDS, every test that was going to report already has
(verbose mode prints one PASSED/FAILED/... line per test as it finishes), so
kill the process tree and decide pass/fail by comparing what printed against
what `--collect-only` said should run. A run where everything printed PASSED
is treated as a pass, loudly labeled as such; anything else is a failure.
"""
import re
import subprocess
import sys
import time

TESTS_DIR = "tests"
TIMEOUT_SECONDS = 90   # generous: every run today finished printing well under 5s

RESULT_RE = re.compile(r"^(?P<nodeid>\S+::\S+)\s+(?P<outcome>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b")


def collect_test_ids() -> list[str]:
    # No extra flags here. pytest.ini's addopts already carries a single -q,
    # which is what makes --collect-only print one `path::test_name` line per
    # test. Adding a second -q on top of it (quiet level 2) collapses that
    # into a one-line-per-FILE count instead, and clearing addopts entirely
    # switches to a `<Module>/<Function>` tree with no `::` in it either --
    # both looked like zero tests were collected and both are wrong.
    r = subprocess.run(
        [sys.executable, "-m", "pytest", TESTS_DIR, "--collect-only"],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr, file=sys.stderr)
        sys.exit("collection failed -- fix that before worrying about the hang")
    return [line for line in r.stdout.splitlines() if "::" in line]


def kill_tree(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        capture_output=True)
    else:
        proc.kill()
    proc.wait(timeout=10)


def main() -> int:
    expected = collect_test_ids()
    print(f"collected {len(expected)} tests")

    # -vv, not -v: pytest.ini's addopts already carries one -q, and verbosity
    # is (count of -v) minus (count of -q) — a single -v exactly cancels it
    # back out to the default dot-per-test output, which the regex below
    # cannot parse. -vv is the smallest count that actually wins.
    proc = subprocess.Popen(
        [sys.executable, "-m", "pytest", TESTS_DIR, "-vv", "-p", "no:cacheprovider"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        bufsize=1,
    )

    lines: list[str] = []
    deadline = time.monotonic() + TIMEOUT_SECONDS
    hung = False
    assert proc.stdout is not None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            hung = True
            break
        line = proc.stdout.readline()
        if line == "" and proc.poll() is not None:
            break
        if line:
            print(line, end="")
            lines.append(line)

    if not hung:
        proc.wait()
        return proc.returncode

    print(f"\n[run_tests] pytest printed no more output for a while; "
          f"killing it after {TIMEOUT_SECONDS}s (this is the known hang, "
          f"see the module docstring)")
    kill_tree(proc)

    outcomes = {}
    for line in lines:
        m = RESULT_RE.match(line)
        if m:
            outcomes[m["nodeid"]] = m["outcome"]

    missing = [t for t in expected if t not in outcomes]
    bad = {k: v for k, v in outcomes.items() if v in ("FAILED", "ERROR", "XPASS")}

    print(f"[run_tests] {len(outcomes)}/{len(expected)} tests reported "
          f"before the hang; {len(bad)} failed")

    if missing:
        print("[run_tests] never reported (treated as failed):")
        for t in missing:
            print("   ", t)
    if bad:
        print("[run_tests] reported failure:")
        for t, o in bad.items():
            print(f"    {o}: {t}")

    if not missing and not bad:
        print("[run_tests] every collected test passed before pytest hung "
              "on shutdown -- treating this run as a PASS")
        return 0

    print("[run_tests] treating this run as a FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
