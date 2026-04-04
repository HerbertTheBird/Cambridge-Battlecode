from cambc import Position, Direction, Controller

_CARDINAL_OFFSETS = ((0, -1), (1, 0), (0, 1), (-1, 0))
_CARDINAL_DIRECTIONS = (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST)

live_ct: Controller

class MockMap:
    """Mock map object to test attribute access overhead."""
    def __init__(self):
        self.width = 60
        self.height = 60

def bench_compare(ct, func1, name1, func2, name2, iterations=1000):
    """
    Runs two benchmark functions, compares their execution times,
    and prints the relative speedup.
    Alternates which function runs first each iteration to avoid cache bias.
    """
    # Warmup runs to ensure bytecode compilation/caching doesn't skew results
    func1(ct, 10)
    func2(ct, 10)

    # Run in alternating halves to reduce cache ordering bias
    half = iterations // 2
    time1 = func1(ct, half)  # func1 first
    time2 = func2(ct, half)
    time2 += func2(ct, iterations - half)  # func2 first
    time1 += func1(ct, iterations - half)

    print(f"--- Comparing: {name1} vs {name2} ({iterations} iters) ---")
    print(f"{name1}: {time1} us")
    print(f"{name2}: {time2} us")

    if time2 > 0 and time1 > 0:
        if time1 > time2:
            print(f"Result: {name2} is {time1 / time2:.2f}x faster than {name1}")
        else:
            print(f"Result: {name1} is {time2 / time1:.2f}x faster than {name2}")
    elif time1 == 0 and time2 == 0:
        print("Result: Both too fast to measure! Increase iterations.")
    print("")

# ==========================================
# Benchmark 1: Position Allocation vs Math
# ==========================================

def benchmark_position_alloc(iterations):
    start_time = live_ct.get_cpu_time_elapsed()
    pos = Position(10, 10)
    dirs = _CARDINAL_DIRECTIONS
    width, height = 60, 60
    
    for _ in range(iterations):
        for d in dirs:
            adj = pos.add(d)
            if 0 <= adj.x < width and 0 <= adj.y < height:
                pass
                
    return live_ct.get_cpu_time_elapsed() - start_time

def benchmark_inline_math(iterations):
    start_time = live_ct.get_cpu_time_elapsed()
    pos = Position(10, 10)
    offsets = _CARDINAL_OFFSETS
    width, height = 60, 60
    
    for _ in range(iterations):
        x, y = pos.x, pos.y
        for dx, dy in offsets:
            nx, ny = x + dx, y + dy
            if 0 <= nx < width and 0 <= ny < height:
                pass
                
    return live_ct.get_cpu_time_elapsed() - start_time

# ==========================================
# Benchmark 2: Distance Method vs Inline
# ==========================================

def benchmark_distance_method(iterations):
    start_time = live_ct.get_cpu_time_elapsed()
    p1 = Position(10, 10)
    p2 = Position(40, 50)
    
    for _ in range(iterations):
        dist = p1.distance_squared(p2)
        
    return live_ct.get_cpu_time_elapsed() - start_time

def benchmark_distance_inline(iterations):
    start_time = live_ct.get_cpu_time_elapsed()
    p1 = Position(10, 10)
    p2 = Position(40, 50)
    
    x1, y1 = p1.x, p1.y
    x2, y2 = p2.x, p2.y
    
    for _ in range(iterations):
        dist = (x1 - x2)**2 + (y1 - y2)**2
        
    return live_ct.get_cpu_time_elapsed() - start_time

# ==========================================
# Benchmark 3: Attribute Access vs Locals
# ==========================================

def benchmark_attribute_access(iterations):
    start_time = live_ct.get_cpu_time_elapsed()
    m = MockMap()
    
    for _ in range(iterations):
        is_valid = 0 <= 30 < m.width and 0 <= 30 < m.height
        
    return live_ct.get_cpu_time_elapsed() - start_time

def benchmark_local_caching(iterations):
    start_time = live_ct.get_cpu_time_elapsed()
    m = MockMap()
    
    w = m.width
    h = m.height
    
    for _ in range(iterations):
        is_valid = 0 <= 30 < w and 0 <= 30 < h
        
    return live_ct.get_cpu_time_elapsed() - start_time

# ==========================================
# Main Runner
# ==========================================

def run_benchmarks(iterations=100):
    """
    Executes all benchmark comparisons.
    Pass a higher iterations count if the microsecond differences are too small.
    """
    print(f"\nRUNNING BENCHMARKS ({iterations} iters)")
    
    bench_compare(live_ct, 
                  benchmark_position_alloc, "Position.add()", 
                  benchmark_inline_math, "Inline Unpacking Math", 
                  iterations=iterations)
                  
    bench_compare(live_ct, 
                  benchmark_distance_method, "distance_squared Method", 
                  benchmark_distance_inline, "Inline Distance Math", 
                  iterations=iterations)
                  
    bench_compare(live_ct, 
                  benchmark_attribute_access, "self.width Attribute Access", 
                  benchmark_local_caching, "Local Variable Caching", 
                  iterations=iterations)
    
# ==========================================
# Live Game Loop Benchmarking
# ==========================================

# Dictionary to store live benchmark data
_live_benchmarks = {}

def bench_reset(ct: Controller):
    """Clears the live benchmark data. Call this at the start of your run() method."""
    _live_benchmarks.clear()
    live_ct = ct  # Store the controller for use in benchmarks

def bench_track(func1, func2, *args, **kwargs):
    """
    Compares two functions in a live game environment for a single iteration.
    Returns the result of func1. 
    
    WARNING: Ensure func1 and func2 do not modify game state (e.g., building/moving),
    as running them back-to-back will cause unintended side effects or GameErrors.
    """
    if func1.__name__ not in _live_benchmarks:
        _live_benchmarks[func1.__name__] = {'time1': 0, 'time2': 0, 'calls': 0}

    calls = _live_benchmarks[func1.__name__]['calls']

    # Alternate execution order on odd/even calls to avoid cache bias
    if calls & 1:
        start2 = live_ct.get_cpu_time_elapsed()
        func2(*args, **kwargs)
        end2 = live_ct.get_cpu_time_elapsed()

        start1 = live_ct.get_cpu_time_elapsed()
        res1 = func1(*args, **kwargs)
        end1 = live_ct.get_cpu_time_elapsed()
    else:
        start1 = live_ct.get_cpu_time_elapsed()
        res1 = func1(*args, **kwargs)
        end1 = live_ct.get_cpu_time_elapsed()

        start2 = live_ct.get_cpu_time_elapsed()
        func2(*args, **kwargs)
        end2 = live_ct.get_cpu_time_elapsed()

    # Store results
    _live_benchmarks[func1.__name__]['time1'] += (end1 - start1)
    _live_benchmarks[func1.__name__]['time2'] += (end2 - start2)
    _live_benchmarks[func1.__name__]['calls'] = calls + 1

    # Return the result of the first function as the source of truth
    return res1

def bench_results():
    """Prints the accumulated results of live comparisons. Call this at the end of your run() method."""
    if not _live_benchmarks:
        print("No live benchmarks recorded.")
        return

    print(f"\nLIVE BENCHMARK RESULTS")
    for name, data in _live_benchmarks.items():
        calls = data['calls']
        if calls == 0:
            continue
        
        avg1 = data['time1'] / calls
        avg2 = data['time2'] / calls
        
        print(f"--- {name} ({calls} calls) ---")
        print(f"Original Avg: {avg1:.2f} us | Total: {data['time1']} us")
        print(f"Modified Avg: {avg2:.2f} us | Total: {data['time2']} us")
        
        if avg1 > 0 and avg2 > 0:
            if avg1 > avg2:
                print(f"Result: Modified is {avg1 / avg2:.2f}x faster")
            elif avg2 > avg1:
                print(f"Result: Original is {avg2 / avg1:.2f}x faster")
            else:
                print("Result: Tied!")
        elif data['time1'] == 0 and data['time2'] == 0:
            print("Result: Both too fast to measure accurately.")