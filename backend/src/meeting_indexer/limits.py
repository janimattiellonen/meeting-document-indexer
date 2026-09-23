"""Running work in a separate process with a hard time limit.

A thread can't be stopped, and a hang inside C code (a malformed PDF in PyMuPDF, a stuck HTTP read)
ignores Python-level timeouts. A child process can always be killed, so the limit always holds.
"""

import multiprocessing
import traceback
from collections.abc import Callable
from multiprocessing.connection import Connection
from typing import Any

# spawn, not fork: forking a process that has loaded macOS system frameworks can crash the child.
_CONTEXT = multiprocessing.get_context("spawn")


class TimeLimitExceeded(Exception):
    pass


class WorkerDied(Exception):
    pass


class WorkerError(Exception):
    """An exception from the worker that couldn't be sent back as itself."""


def _run(connection: Connection, fn: Callable[..., Any], args: tuple) -> None:
    try:
        connection.send((True, fn(*args), None))
    except BaseException as e:
        details = traceback.format_exc()
        try:
            connection.send((False, e, details))
        except Exception:  # the exception itself can't be pickled
            connection.send((False, WorkerError(f"{type(e).__name__}: {e}"), details))
    finally:
        connection.close()


def run_with_time_limit(fn: Callable[..., Any], *args: Any, seconds: float) -> Any:
    """fn(*args) in a child process. Raises TimeLimitExceeded, and kills the child, if it takes longer.

    fn and args must be picklable (a module-level function). An exception raised by fn is re-raised
    here with the child's traceback text in its `worker_traceback` attribute.
    """
    receiver, sender = _CONTEXT.Pipe(duplex=False)
    process = _CONTEXT.Process(target=_run, args=(sender, fn, args), daemon=True)
    process.start()
    sender.close()
    try:
        if not receiver.poll(seconds):
            raise TimeLimitExceeded(f"did not finish within the {seconds:.0f} s time limit")
        try:
            ok, value, details = receiver.recv()
        except EOFError:
            process.join(5)
            raise WorkerDied(f"worker process died without a result (exit code {process.exitcode})") from None
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join()
        receiver.close()
    if ok:
        return value
    value.worker_traceback = details
    raise value
