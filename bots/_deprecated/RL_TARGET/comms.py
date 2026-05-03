from cambc import Controller, Position


video_frames = []
current_frame_index = 0


def load_video_frames(filename="lines.txt"):
    frames = []
    current_frame = []

    with open(filename, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()

            # Blank line = end of one frame
            if line == "":
                frames.append(current_frame)
                current_frame = []
                continue

            numbers = [int(x.strip()) for x in line.strip("[]").split(",")]
            current_frame.append(numbers)

    # In case file doesn't end with a blank line
    if current_frame:
        frames.append(current_frame)

    return frames

video_width = 0
video_height = 0


def compute_video_size():
    global video_width, video_height, video_frames

    max_x = 0
    max_y = 0

    for frame in video_frames:
        for item in frame:
            x1, y1, x2, y2 = item
            max_x = max(max_x, x1, x2)
            max_y = max(max_y, y1, y2)

    video_width = max_x + 1
    video_height = max_y + 1
    
def init(filename="bots/baseline/lines.txt"):
    global video_frames, current_frame_index
    video_frames = load_video_frames(filename)
    compute_video_size()
    current_frame_index = 0


def communicate(rc: Controller):
    global current_frame_index, video_frames, video_width, video_height

    if not video_frames:
        return

    frame = video_frames[current_frame_index]

    center_x = rc.get_map_width() / 2
    center_y = -25

    offset_x = int(center_x - video_width / 2)
    offset_y = int(center_y - video_height / 2)

    for item in frame:
        x1, y1, x2, y2 = item

        rc.draw_indicator_line(
            Position(x1 + offset_x, y1 + offset_y),
            Position(x2 + offset_x, y2 + offset_y),
            255, 255, 255
        )

    current_frame_index += 1
    if current_frame_index >= len(video_frames):
        current_frame_index = 0