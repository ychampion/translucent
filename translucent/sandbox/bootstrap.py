"""Image-owned entrypoint; runs before any agent code, as UID 10001."""

import ctypes
import os
import resource
import runpy
import sys


def main():
    if os.getuid() != 10001 or os.geteuid() != 10001 or os.getgroups():
        raise RuntimeError("analysis must run as UID 10001 with no supplementary groups")
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # Prevent exec of a setuid binary from regaining the supervisor's privileges.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0):  # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS failed")
    os.umask(0o077)
    os.chdir("/work")
    sys.argv = ["/work/code.py"]
    runpy.run_path(sys.argv[0], run_name="__main__")


if __name__ == "__main__":
    main()
