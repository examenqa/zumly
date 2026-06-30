import argparse
import sys
import logging
import json
from zumly.app.video_exporter import VideoExporter
from zumly.app.models import RecordingSession
from zumly.app.backgrounds import DEFAULT_PRESET as DEFAULT_BG, PRESETS as BG_PRESETS
from zumly.app.frames import DEFAULT_FRAME, FRAME_PRESETS
from zumly.app.models import DEFAULT_CLICK_EFFECT, CLICK_EFFECT_PRESETS

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')


def _preset_by_name(presets, name: str, default):
    """Resolve a persisted preset name against a preset list."""
    return next((preset for preset in presets if preset.name == name), default)

def main() -> None:
    parser = argparse.ArgumentParser(description="Zumly Headless Exporter")
    parser.add_argument("--project", type=str, required=True, help="Path to the project JSON")
    args = parser.parse_args()

    with open(args.project, "r", encoding="utf-8") as f:
        data = json.load(f)

    session = RecordingSession.from_json(json.dumps(data))
    
    out_path = data.get("outPath", "")
    video_path = data.get("videoPath", "")
    monitor_rect = data.get("monitorRect", {})
    actual_fps = data.get("actualFps", 30.0)

    if not out_path or not video_path:
        logging.error("Project JSON missing outPath or videoPath")
        sys.exit(1)
        
    # Resolve aesthetics
    bg_preset = _preset_by_name(BG_PRESETS, session.background_id or "", DEFAULT_BG)
    frame_preset = _preset_by_name(FRAME_PRESETS, session.frame_id or "", DEFAULT_FRAME)
    click_preset = _preset_by_name(
        CLICK_EFFECT_PRESETS, session.click_effect_id or "", DEFAULT_CLICK_EFFECT
    )

    target_res = None
    if session.output_dimensions and isinstance(session.output_dimensions, list) and len(session.output_dimensions) == 2:
        target_res = tuple(session.output_dimensions)
    elif session.output_dimensions == "auto":
        target_res = None

    def on_progress(p: float) -> None:
        sys.stdout.write(f"\rExport progress: {p*100:.1f}%")
        sys.stdout.flush()

    def on_finished(path: str) -> None:
        print(f"\nExport finished: {path}")

    def on_error(err: str) -> None:
        print(f"\nExport error: {err}")

    exporter = VideoExporter(
        progress_cb=on_progress,
        finished_cb=on_finished,
        error_cb=on_error,
    )
    
    # Block until done
    import time
    export_done = [False]
    def on_finished_wrapper(path: str) -> None:
        on_finished(path)
        export_done[0] = True
    def on_error_wrapper(err: str) -> None:
        on_error(err)
        export_done[0] = True

    exporter._finished_cb = on_finished_wrapper
    exporter._error_cb = on_error_wrapper

    exporter.export(
        input_path=video_path,
        output_path=out_path,
        keyframes=session.keyframes,
        actual_fps=actual_fps,
        mouse_track=session.mouse_track,
        monitor_rect=monitor_rect,
        bg_preset=bg_preset,
        frame_preset=frame_preset,
        target_resolution=target_res,
        click_events=session.click_events,
        click_preset=click_preset,
        duration_ms=session.duration,
        frame_timestamps=session.frame_timestamps,
        encoder_id="libx264",
        video_segments=session.video_segments,
        highlights=session.highlights,
    )

    while not export_done[0]:
        time.sleep(0.5)

    logging.info("Headless export done.")

if __name__ == "__main__":
    main()
