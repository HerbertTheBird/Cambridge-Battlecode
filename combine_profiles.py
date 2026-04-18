import re
from collections import defaultdict
import glob

# Change this to your folder / pattern
FILES = glob.glob("profiles/*.txt")

# function -> [ncalls, tottime, cumtime]
data = defaultdict(lambda: [0, 0.0, 0.0])

header_totals = {
    "timed_out_turns": 0,
    "total_calls": 0,
    "total_tottime": 0.0,
    "total_cumtime": 0.0,
}

row_pattern = re.compile(
    r"\s*(\d+)\s+([\d.]+)\s+([\d.]+)\s+(.*)"
)

for file in FILES:
    with open(file, "r") as f:
        for line in f:
            line = line.strip()

            # Header aggregation
            if line.startswith("Timed-out turns:"):
                header_totals["timed_out_turns"] += int(line.split(":")[1])
            elif line.startswith("Total calls:"):
                header_totals["total_calls"] += int(line.split(":")[1])
            elif line.startswith("Total tottime:"):
                header_totals["total_tottime"] += float(line.split(":")[1].split()[0])
            elif line.startswith("Total cumtime:"):
                header_totals["total_cumtime"] += float(line.split(":")[1].split()[0])

            # Table rows
            else:
                m = row_pattern.match(line)
                if m:
                    ncalls = int(m.group(1))
                    tottime = float(m.group(2))
                    cumtime = float(m.group(3))
                    func = m.group(4)

                    data[func][0] += ncalls
                    data[func][1] += tottime
                    data[func][2] += cumtime

# Sort by average cumulative time (descending)
sorted_rows = sorted(
    data.items(),
    key=lambda x: (x[1][2] / x[1][0]) if x[1][0] > 0 else 0,
    reverse=True
)

# Output
with open("combined_profile.txt", "w") as out:
    out.write("Combined profile\n")
    out.write(f"Timed-out turns: {header_totals['timed_out_turns']}\n")
    out.write(f"Total calls: {header_totals['total_calls']}\n")
    out.write(f"Total tottime: {header_totals['total_tottime']:.3f} us\n")
    out.write(f"Total cumtime: {header_totals['total_cumtime']:.3f} us\n\n")

    out.write(
        f"{'ncalls':>12} {'tottime_us':>14} {'cumtime_us':>14} {'avg_cum_us':>14}  function\n"
    )
    out.write("-" * 110 + "\n")

    for func, (ncalls, tottime, cumtime) in sorted_rows:
        avg_cum = cumtime / ncalls if ncalls > 0 else 0.0
        out.write(
            f"{ncalls:12d} {tottime:14.3f} {cumtime:14.3f} {avg_cum:14.3f}  {func}\n"
        )

print("Done! Output written to combined_profile.txt")