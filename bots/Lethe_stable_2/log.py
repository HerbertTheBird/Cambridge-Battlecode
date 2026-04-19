DEBUG_LOGGING = False
DRAW_DEBUG = False

if DEBUG_LOGGING:
    def log(*args, **kwargs):
        print(*args, **kwargs)
else:
    def log(*args, **kwargs):
        pass