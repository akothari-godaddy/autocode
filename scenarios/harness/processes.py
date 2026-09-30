"""Bounded CLI calls that retain ownership when a provider starts a new session."""
from __future__ import annotations

import subprocess

try:
    import psutil
except ModuleNotFoundError as error:
    if error.name != "psutil":
        raise
    psutil = None


class SupervisionUnavailable(RuntimeError):
    """A CLI must not launch without the dependency needed to stop its workers."""


class CallTimeout(subprocess.TimeoutExpired):
    """A timed-out invocation, including any uncertainty about worker cleanup."""

    def __init__(self, command, timeout, errors):
        super().__init__(command, timeout)
        self.cleanup_errors = errors


def _live(process):
    # Process objects retain birth identity; is_running rejects reused PIDs.
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _capture(process, owned, errors):
    try:
        if process.is_running():
            for child in process.children(recursive=True):
                if child not in owned:
                    owned.append(child)
    except psutil.NoSuchProcess:
        pass
    except (psutil.Error, OSError) as error:
        errors.append(f"cannot capture descendants of PID {process.pid}: {error}")


def _signal(process, method, errors):
    try:
        if _live(process):
            # psutil checks the saved identity again before sending the signal.
            getattr(process, method)()
    except psutil.NoSuchProcess:
        pass
    except (psutil.Error, OSError) as error:
        errors.append(f"cannot {method} owned PID {process.pid}: {error}")


def _stop(child, parent, identity_error):
    errors = []
    owned = [parent] if parent else []
    if parent:
        # A detached session is still a descendant until the CLI dies.
        _capture(parent, owned, errors)
        _signal(parent, "terminate", errors)
    else:
        errors.append(identity_error)
        # Popen still owns its unreaped direct child, even without psutil metadata.
        try:
            child.terminate()
        except ProcessLookupError:
            pass
        except OSError as error:
            errors.append(f"cannot terminate CLI PID {child.pid}: {error}")
    try:
        child.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    except BaseException as error:
        # Decoding failures and a second interrupt must not skip worker cleanup.
        errors.append(f"cannot collect CLI output during graceful cleanup: {type(error).__name__}: {error}")
    # Retain new descendants created during graceful termination as well.
    for process in list(owned):
        _capture(process, owned, errors)
    for process in reversed(owned):
        _signal(process, "kill", errors)
    if not parent:
        try:
            child.kill()
        except ProcessLookupError:
            pass
        except OSError as error:
            errors.append(f"cannot kill CLI PID {child.pid}: {error}")
    try:
        _, alive = psutil.wait_procs(owned, timeout=2)
        for process in alive:
            if _live(process):
                errors.append(f"owned PID {process.pid} remains alive after cleanup")
    except (psutil.Error, OSError) as error:
        errors.append(f"cannot verify owned worker cleanup: {error}")
    try:
        child.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        errors.append("CLI output pipes remain open after cleanup; an uncaptured worker may remain")
    except BaseException as error:
        errors.append(f"cannot collect CLI output after cleanup: {type(error).__name__}: {error}")
    finally:
        for stream in (child.stdout, child.stderr):
            if stream:
                try:
                    stream.close()
                except OSError as error:
                    errors.append(f"cannot close CLI output pipe: {error}")
    return errors


def run_cli(command, *, env, cwd, timeout):
    """Run one CLI; on timeout stop only processes captured from its ancestry."""
    if psutil is None:
        raise SupervisionUnavailable("CLI process supervision requires psutil in the harness interpreter, "
                                     "including with --autocode; run scenarios/run.py with the project's "
                                     "virtualenv Python (.venv/bin/python)")
    child = subprocess.Popen(command, env=env, cwd=cwd, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    identity_error = None
    try:
        parent = psutil.Process(child.pid)
    except (psutil.Error, OSError) as error:
        parent = None
        identity_error = f"cannot capture CLI PID {child.pid} identity: {error}"
    try:
        stdout, stderr = child.communicate(timeout=timeout)
        return subprocess.CompletedProcess(command, child.returncode, stdout, stderr)
    except BaseException as error:
        errors = _stop(child, parent, identity_error)
        if isinstance(error, subprocess.TimeoutExpired):
            raise CallTimeout(command, timeout, errors) from None
        if errors:
            error.add_note("CLI cleanup incomplete: " + "; ".join(errors))
        raise
