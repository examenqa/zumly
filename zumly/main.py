import argparse
import logging
import os
import sys
import time

from app.screen_recorder import ScreenRecorder
from app.mouse_tracker import MouseTracker
from app.click_tracker import ClickTracker
from app.keyboard_tracker import KeyboardTracker
from app.global_hotkeys import GlobalHotkeys
from app.activity_analyzer import analyze_activity

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def main() -> None:
    parser = argparse.ArgumentParser(description="Zumly Headless CLI")
    parser.add_argument("--out", "-o", required=True, help="Output MP4 path")
    parser.add_argument("--monitor", "-m", type=int, default=1, help="Monitor index (default 1)")
    parser.add_argument("--fps", type=int, default=60, help="Recording FPS")
    parser.add_argument("--duration", "-d", type=float, default=0.0, help="Optional duration to record in seconds (if 0, stops on hotkey CTRL+SHIFT+R)")
    parser.add_argument("--stop-file", type=str, default="", help="Optional file path used by the tray app to request a graceful stop")
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
    
    # Setup Hotkey Tracker (Callback based)
    recording_toggled = [False]
    def on_hotkey_triggered():
        recording_toggled[0] = True
        
    hotkey_tracker = GlobalHotkeys(callback=on_hotkey_triggered)
    
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
    if not args.stop_file:
        hotkey_tracker.register_record_hotkey()
    
    from app.recording_overlay import RecordingOverlay
    overlay = RecordingOverlay(monitor_rect)
    overlay.start()
    
    raw_video_path = recorder.start_recording(start_time=start_time_ms/1000.0)
    logging.info(f"Recording started. Outputting raw video to: {raw_video_path}")
    if args.duration > 0:
        logging.info(f"Recording will stop automatically after {args.duration} seconds.")
    else:
        logging.info("Press CTRL+SHIFT+R to stop recording.")
    
    try:
        start_t = time.time()
        while True:
            time.sleep(0.1)
            if args.stop_file and os.path.exists(args.stop_file):
                logging.info("Stop file detected. Stopping recording...")
                break
            # Check duration
            if args.duration > 0 and (time.time() - start_t) >= args.duration:
                logging.info(f"Reached duration of {args.duration}s. Stopping recording...")
                break
            # Check if hotkey pressed
            if recording_toggled[0]:
                logging.info("Hotkey pressed. Stopping recording...")
                break
    except KeyboardInterrupt:
        logging.info("Ctrl+C pressed. Stopping recording...")
        
    # Stop trackers and recorder
    mouse_events = mouse_tracker.stop()
    click_events = click_tracker.stop()
    kbd_events = kbd_tracker.stop()
    hotkey_tracker.unregister_record_hotkey()
    overlay.stop()
    recorder.stop_recording()
    recorder.stop_capture()
    if args.stop_file:
        try:
            os.remove(args.stop_file)
        except OSError:
            pass
    
    logging.info("Recording stopped. Generating AI auto-zooms...")
    
    # Generate zoom keyframes
    keyframes = analyze_activity(
        mouse_track=mouse_events,
        monitor_rect=monitor_rect,
        key_events=kbd_events,
        click_events=click_events
    )
    
    logging.info(f"Generated {len(keyframes)} zoom keyframes. Saving session state...")
    
    import uuid
    import json
    from app.models import RecordingSession

    session_id = str(uuid.uuid4())
    duration_ms = recorder.recording_duration_ms

    session = RecordingSession(
        id=session_id,
        start_time=start_time_ms / 1000.0,
        duration=duration_ms,
        mouse_track=mouse_events,
        keyframes=keyframes,
        click_events=click_events,
        frame_timestamps=recorder.frame_timestamps,
    )

    data = json.loads(session.to_json())
    data["monitorRect"] = monitor_rect
    data["actualFps"] = recorder.actual_fps
    data["videoPath"] = raw_video_path
    data["outPath"] = args.out # Preserve intended export path

    timestamp = int(start_time_ms)
    out_dir = os.path.dirname(args.out) or "."
    project_path = os.path.join(out_dir, f"{timestamp}_project.json")
    
    with open(project_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    logging.info(f"Session serialized to {project_path}")
    logging.info("Force exiting to release WGC hooks...")
    sys.exit(0)

if __name__ == "__main__":
    main()
