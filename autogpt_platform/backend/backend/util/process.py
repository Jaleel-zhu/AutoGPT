import logging
import os
import signal
import sys
from abc import ABC, abstractmethod
from multiprocessing import Process, get_all_start_methods, set_start_method
from typing import Optional

from backend.util.logging import configure_logging
from backend.util.metrics import sentry_init

logger = logging.getLogger(__name__)
_SERVICE_NAME = "MainProcess"


def get_service_name():
    return _SERVICE_NAME


def set_service_name(name: str):
    global _SERVICE_NAME
    _SERVICE_NAME = name


class AppProcess(ABC):
    """
    A class to represent an object that can be executed in a background process.
    """

    process: Optional[Process] = None
    cleaned_up = False

    if "forkserver" in get_all_start_methods():
        set_start_method("forkserver", force=True)
    else:
        logger.warning("Forkserver start method is not available. Using spawn instead.")
        set_start_method("spawn", force=True)

    configure_logging()
    sentry_init()

    # Methods that are executed INSIDE the process #

    @abstractmethod
    def run(self):
        """
        The method that will be executed in the process.
        """
        pass

    @classmethod
    @property
    def service_name(cls) -> str:
        return cls.__name__

    @abstractmethod
    def cleanup(self):
        """
        Implement this method on a subclass to do post-execution cleanup,
        e.g. disconnecting from a database or terminating child processes.
        """
        pass

    def health_check(self) -> str:
        """
        A method to check the health of the process.
        """
        return "OK"

    def execute_run_command(self, silent):
        signal.signal(signal.SIGTERM, self._self_terminate)
        signal.signal(signal.SIGINT, self._self_terminate)

        try:
            if silent:
                sys.stdout = open(os.devnull, "w")
                sys.stderr = open(os.devnull, "w")

            set_service_name(self.service_name)
            logger.info(f"[{self.service_name}] Starting...")
            self.run()
        except (KeyboardInterrupt, SystemExit) as e:
            logger.warning(f"[{self.service_name}] Terminated: {e}; quitting...")
        finally:
            if not self.cleaned_up:
                self.cleanup()
                self.cleaned_up = True
            logger.info(f"[{self.service_name}] Terminated.")

    def _self_terminate(self, signum: int, frame):
        if not self.cleaned_up:
            self.cleanup()
            self.cleaned_up = True
        sys.exit(0)

    # Methods that are executed OUTSIDE the process #

    def __enter__(self):
        self.start(background=True)
        return self

    def __exit__(self, *args, **kwargs):
        self.stop()

    def start(self, background: bool = False, silent: bool = False, **proc_args) -> int:
        """
        Start the background process.
        Args:
            background: Whether to run the process in the background.
            silent: Whether to disable stdout and stderr.
            proc_args: Additional arguments to pass to the process.
        Returns:
            the process id or 0 if the process is not running in the background.
        """
        if not background:
            self.execute_run_command(silent)
            return 0

        self.process = Process(
            name=self.__class__.__name__,
            target=self.execute_run_command,
            args=(silent,),
            **proc_args,
        )
        self.process.start()
        self.health_check()
        logger.info(f"[{self.service_name}] started with PID {self.process.pid}")

        return self.process.pid or 0

    def stop(self):
        """
        Stop the background process.
        """
        if not self.process:
            return

        self.process.terminate()
        self.process.join()

        logger.info(f"[{self.service_name}] with PID {self.process.pid} stopped")
        self.process = None
