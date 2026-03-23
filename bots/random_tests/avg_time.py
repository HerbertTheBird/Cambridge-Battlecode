import sys

def sum_and_average(filename):
    total = 0.0
    count = 0

    with open(filename, "r") as f:
        for line in f:
            line = line.strip()
            if line:  # skip empty lines
                try:
                    value = float(line)
                    total += value
                    count += 1
                except ValueError:
                    print(f"Warning: '{line}' is not a valid number and will be skipped.", file=sys.stderr)

    average = total / count if count > 0 else 0.0
    return total, average


if __name__ == "__main__":

    filename = "time.txt"
    total, average = sum_and_average(filename)

    print("Sum:", total)
    print("Average:", average)
