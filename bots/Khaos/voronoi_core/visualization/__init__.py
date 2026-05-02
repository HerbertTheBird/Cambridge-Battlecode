try:
    from voronoi_core.visualization.visualizer import Presets
    from voronoi_core.visualization.visualizer import Visualizer
except Exception:  # pragma: no cover - optional visualization extras
    Presets = None
    Visualizer = None
