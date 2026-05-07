DEBUG_LOGGING = False
CHOKEPOINT_DRAW_DEBUG = True
DRAW_DEBUG = True

if DEBUG_LOGGING:
    def log(*args, **kwargs):
        print(*args, **kwargs)
else:
    def log(*args, **kwargs):
        pass
