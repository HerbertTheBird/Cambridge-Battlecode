try:
    from foronoi.visualization.visualizer import Presets
    from foronoi.visualization.visualizer import Visualizer
except Exception:  # pragma: no cover - optional visualization extras
    Presets = None
    Visualizer = None
