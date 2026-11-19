"""Route POSIX group signals to the owned process double in preview tests."""

from bot.preview_capture.process import ProcessController, SIGKILL, SIGTERM


def fake_process_controller(process, **options):
    def signal_group(pid, signum):
        if pid != process.pid:
            raise AssertionError(f"Unexpected process group {pid}")
        if signum == SIGTERM:
            process.terminate()
        elif signum == SIGKILL:
            process.kill()
        else:
            raise AssertionError(f"Unexpected group signal {signum}")

    return ProcessController(process, killpg=signal_group, **options)
