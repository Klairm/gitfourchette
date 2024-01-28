import logging
import os
import time

logger = logging.getLogger(__name__)
BENCHMARK_LOGGING_LEVEL = 5

try:
    import psutil
except ModuleNotFoundError:
    logger.info("psutil isn't available. Memory pressure estimates won't work.")
    psutil = None


def getRSS():
    if psutil:
        return psutil.Process(os.getpid()).memory_info().rss
    else:
        return 0


class Benchmark:
    """ Context manager that reports how long a piece of code takes to run. """

    nesting = []

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        Benchmark.nesting.append(self.name)
        self.rssAtStart = getRSS()
        self.start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        tt = time.perf_counter() - self.start
        rss = getRSS()
        logger.log(BENCHMARK_LOGGING_LEVEL, f"{tt*1000:8.2f} ms {(rss - self.rssAtStart) // 1024:6,d}K {'/'.join(Benchmark.nesting)}")
        Benchmark.nesting.pop()


def benchmark(func):
    """ Decorator that reports how long a function takes to run. """
    def wrapper(*args, **kwargs):
        with Benchmark(func.__name__):
            return func(*args, **kwargs)
    return wrapper
