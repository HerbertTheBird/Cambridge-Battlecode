from cambc import Controller

DEBUG_LOGGING = False
TIME_LOGGING = False


if DEBUG_LOGGING:
    def log(*args, **kwargs):
        print(*args, **kwargs)
else:
    def log(*args, **kwargs):
        pass


def log_time(ct: Controller, message: str):
    if DEBUG_LOGGING and TIME_LOGGING:
        log(f"{message}: {ct.get_cpu_time_elapsed()} \u03bcs")
