import argparse
import logging
import sys
import time

from app.screen_recorder import ScreenRecorder
from app.mouse_tracker import MouseTracker
from app.click_tracker import ClickTracker
from app.keyboard_tracker import KeyboardTracker
from app.global_hotkeys import GlobalHotkeyTracker
from app.activity_analyzer import analyze_activity
from app.video_exporter import VideoExporter
from app.backgrounds import DEFAULT_PRESET as DEFAULT_BG
from app.frames import DEFAULT_FRAME
from app.models import DEFAULT_CLICK_EFFECT

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def main() -> None:
    parser = argparse.ArgumentParser(description="Zumly Headless CLI")
    parser.add_argument("--out", "-o", required=True, help="Output MP4 path")
    parser.add_argument("--monitor", "-m", type=int, default=1, help="Monitor index (default 1)")
    parser.add_argument("--fps", type=int, default=60, help="Recording FPS")
    parser.add_argument("--duration", "-d", type=float, default=0.0, help="Optional duration to record in seconds (if 0, stops on hotkey CTRL+ALT+R)")
    args = parser.parse_args()

    # Determine monitor dimensions
    monitor_rect = {}
    for mon in ScreenRecorder.get_monitors():
        if mon["index"] == args.monitor:
            monitor_rect = mon
            break
            
    if not monitor_rect:
        logging.error(f"Could not find monitor with index {args.monitor}")
        sys.exit(1)

    # Trackers
    mouse_tracker = MouseTracker()
    click_tracker = ClickTracker()
    kbd_tracker = KeyboardTracker()
    hotkey_tracker = GlobalHotkeyTracker()
    
    # Initialize recorder
    def on_recording_finished(path: str) -> None:
        pass
    def on_capture_backend_changed(backend: str) -> None:
        pass
        
    recorder = ScreenRecorder(
        recording_finished_cb=on_recording_finished,
        capture_backend_changed_cb=on_capture_backend_changed
    )
    
    logging.info(f"Starting capture on monitor {args.monitor} at {args.fps} FPS...")
    recorder.start_capture(args.monitor, args.fps)
    
    # Let capture spin up
    time.sleep(2.0)
    
    # Start tracking and recording
    start_time_ms = time.time() * 1000
    mouse_tracker.start(start_time_ms)
    click_tracker.start(start_time_ms)
    kbd_tracker.start(start_time_ms)
    hotkey_tracker.start()
    
    raw_video_path = recorder.start_recording(start_time=start_time_ms/1000.0)
    logging.info(f"Recording started. Outputting raw video to: {raw_video_path}")
    if args.duration > 0:
        logging.info(f"Recording will stop automatically after {args.duration} seconds.")
    else:
        logging.info("Press CTRL+ALT+R to stop recording.")
    
    try:
        start_t = time.time()
        while True:
            time.sleep(0.1)
            # Check duration
            if args.duration > 0 and (time.time() - start_t) >= args.duration:
                logging.info(f"Reached duration of {args.duration}s. Stopping recording...")
                break
            # Check if hotkey pressed
            if hotkey_tracker.pop_recording_toggle():
                logging.info("Hotkey pressed. Stopping recording...")
                break
    except KeyboardInterrupt:
        logging.info("Ctrl+C pressed. Stopping recording...")
        
    # Stop trackers and recorder
    mouse_events = mouse_tracker.stop()
    click_events = click_tracker.stop()
    kbd_events = kbd_tracker.stop()
    hotkey_tracker.stop()
    recorder.stop_recording()
    recorder.stop_capture()
    
    logging.info("Recording stopped. Generating AI auto-zooms...")
    
    # Generate zoom keyframes
    keyframes = analyze_activity(
        mouse_track=mouse_events,
        monitor_rect=monitor_rect,
        key_events=kbd_events,
        click_events=click_events
    )
    
    logging.info(f"Generated {len(keyframes)} zoom keyframes. Exporting video...")
    
    # Export
    def on_progress(p: float) -> None:
        sys.stdout.write(f"\rExport progress: {p*100:.1f}%")
        sys.stdout.flush()
        
    def on_finished(path: str) -> None:
        print(f"\nExport finished: {path}")
        
    def on_error(err: str) -> None:
        print(f"\nExport error: {err}")
        
    def on_status(msg: str) -> None:
        # print(f"\nStatus: {msg}")
        pass

    exporter = VideoExporter(
        progress_cb=on_progress,
        finished_cb=on_finished,
        error_cb=on_error,
        status_cb=on_status
    )
            
    # Need to wait for export
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
        input_path=raw_video_path,
        output_path=args.out,
        keyframes=keyframes,
        actual_fps=recorder.actual_fps,
        mouse_track=mouse_events,
        monitor_rect=monitor_rect,
        bg_preset=DEFAULT_BG,
        frame_preset=DEFAULT_FRAME,
        click_events=click_events,
        click_preset=DEFAULT_CLICK_EFFECT,
        output_dim="auto",
        duration_ms=recorder.recording_duration_ms,
        frame_timestamps=recorder.frame_timestamps,
        encoder_id="libx264"
    )
    
    while not export_done[0]:
        time.sleep(0.5)
        
    logging.info("Done.")

if __name__ == "__main__":
    main()

