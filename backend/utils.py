# Training YOLOv12 for detecting pathhole severity using a custom dataset

def severity_from_area_and_position(area_px: float, y_center: float, frame_h: int, alpha: float = 0.6) -> str:
    """
    Heuristic severity score: combines normalized box area and vertical position.
    Similar idea to the post: bigger + closer-to-bottom => more severe. :contentReference[oaicite:7]{index=7}
    """
    vertical_norm = y_center / max(frame_h, 1)
    area_norm = area_px / max(frame_h * frame_h, 1)
    score = alpha * area_norm + (1 - alpha) * vertical_norm

    if score < 0.2:
        return "Low"
    elif score < 0.4:
        return "Medium"
    return "High"
    
# This helper function determines whether a given path corresponds to
# a video file by checking its extension
def is_video_file(path):
    return isinstance(path, str) and path.endswith((".mp4", ".avi", ".mov"))