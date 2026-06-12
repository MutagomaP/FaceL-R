# src/camera.py
"""Open a local USB camera or ESP32 MJPEG stream."""
from __future__ import annotations

import argparse
import socket
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import cv2

MDNS_HOST = "esp32cam.local"
ESP32_PROBE_PATHS = ("/", "/stream")
# External USB robot cam is usually index 1; index 0 is often the laptop built-in
DEFAULT_USB_CAMERA = 1


def _frame_brightness(frame) -> float:
    if frame is None or frame.size == 0:
        return 0.0
    return float(frame.mean())


def _frame_is_blank(frame, threshold: float = 1.0) -> bool:
    return _frame_brightness(frame) < threshold


def _open_usb_capture(index: int) -> cv2.VideoCapture:
    """Open a USB camera with Windows-friendly settings."""
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap = cv2.VideoCapture(index)
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    return cap


def _read_usb_frame(cap: cv2.VideoCapture, warmup: int = 20):
    for _ in range(warmup):
        cap.read()
    return cap.read()


def list_cameras(max_index: int = 5) -> list[tuple[int, tuple[int, int, int] | None, float]]:
    """Return [(index, shape, brightness), ...] for each camera that opens."""
    found: list[tuple[int, tuple[int, int, int] | None, float]] = []
    for i in range(max_index + 1):
        cap = _open_usb_capture(i)
        if not cap.isOpened():
            cap.release()
            continue
        ok, frame = _read_usb_frame(cap, warmup=10)
        shape = tuple(frame.shape) if ok and frame is not None else None
        brightness = _frame_brightness(frame) if ok and frame is not None else 0.0
        cap.release()
        found.append((i, shape, brightness))
    return found


def print_cameras(max_index: int = 5) -> None:
    cameras = list_cameras(max_index)
    if not cameras:
        print("No USB cameras found.")
        return
    for idx, shape, brightness in cameras:
        if not shape:
            status = "no frame"
        elif brightness < 1.0:
            status = f"{shape[1]}x{shape[0]} BLANK (virtual/disabled - do not use)"
        else:
            status = f"{shape[1]}x{shape[0]} OK brightness={brightness:.0f}"
        print(f"  [{idx}] {status}")
    print(
        "\nRobot ESP32 camera is NOT a USB index - use:\n"
        "  --camera http://<ESP32-IP>/stream"
    )


def _local_subnet_prefix() -> str | None:
    """Return '192.168.1' from a PC address like 192.168.1.102."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except OSError:
        return None
    parts = ip.split(".")
    if len(parts) != 4:
        return None
    return ".".join(parts[:3])


def _is_esp32_root(body: str) -> bool:
    lower = body.lower()
    return "esp32" in lower and "stream" in lower


def _probe_host(host: str, timeout: float = 0.8) -> str | None:
    for path in ESP32_PROBE_PATHS:
        url = f"http://{host}{path}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "FaceLockingServo/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if path == "/stream":
                    return f"http://{host}/stream"
                body = resp.read(256).decode("utf-8", errors="ignore")
                if _is_esp32_root(body):
                    return f"http://{host}/stream"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            continue
    return None


def discover_esp32_stream(timeout: float = 0.8) -> str | None:
    """
    Find an ESP32-CAM MJPEG server on the local subnet.
    Tries mDNS hostname first, then scans the /24 around this PC's IP.
    """
    mdns = _probe_host(MDNS_HOST, timeout=timeout)
    if mdns:
        return mdns

    prefix = _local_subnet_prefix()
    if not prefix:
        return None

    print(f"Scanning {prefix}.1-254 for ESP32-CAM (may take ~30s)...", flush=True)
    candidates = [f"{prefix}.{i}" for i in range(1, 255)]

    with ThreadPoolExecutor(max_workers=40) as pool:
        futures = {pool.submit(_probe_host, host, timeout): host for host in candidates}
        for fut in as_completed(futures):
            found = fut.result()
            if found:
                for pending in futures:
                    pending.cancel()
                return found
    return None


def probe_stream_url(url: str, timeout: float = 3.0) -> bool:
    """Quick HTTP check before OpenCV opens the MJPEG stream."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FaceLockingServo/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def _fix_stream_url_subnet(url: str) -> str:
    """Rewrite stream host to this PC's /24 (e.g. 192.168.0.50 -> 192.168.1.50)."""
    pc_prefix = _local_subnet_prefix()
    if not pc_prefix:
        return url
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    parts = host.split(".")
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        return url
    if ".".join(parts[:3]) == pc_prefix:
        return url
    fixed_host = f"{pc_prefix}.{parts[3]}"
    fixed = url.replace(host, fixed_host, 1)
    print(
        f"Auto-corrected stream URL: {host} -> {fixed_host} "
        f"(your PC is on {pc_prefix}.x, Tecno pop)",
        flush=True,
    )
    return fixed


def _subnet_mismatch_hint(url: str) -> str | None:
    """Warn when the stream IP is on a different /24 than this PC."""
    try:
        host = urllib.request.urlparse(url).hostname or ""
        parts = host.split(".")
        if len(parts) != 4 or not all(p.isdigit() for p in parts):
            return None
        pc_prefix = _local_subnet_prefix()
        url_prefix = ".".join(parts[:3])
        if pc_prefix and url_prefix != pc_prefix:
            return (
                f"Subnet mismatch: your PC is on {pc_prefix}.x but the URL uses {host}.\n"
                f"  Connect the PC to the same Wi-Fi as the ESP32 (see sketch ssid), "
                f"or use the IP from Serial Monitor."
            )
    except (ValueError, AttributeError):
        pass
    return None


def open_camera(
    source: str | int = 0,
    timeout_ms: int = 10000,
    auto_discover: bool = False,
) -> cv2.VideoCapture:
    """
    Open camera by USB index or HTTP URL.

    Examples:
        open_camera(0)
        open_camera("2")
        open_camera("http://192.168.1.50/stream")
        open_camera("http://192.168.1.50/stream", auto_discover=True)
    """
    if isinstance(source, int):
        index = source
    elif isinstance(source, str) and source.lstrip("-").isdigit():
        index = int(source)
    else:
        original = str(source)
        url = _fix_stream_url_subnet(original)
        if not probe_stream_url(url, timeout=timeout_ms / 1000.0):
            mismatch = _subnet_mismatch_hint(original)
            if mismatch:
                print(mismatch, flush=True)
            elif url != original:
                print(f"Tried {url} (corrected from {original})", flush=True)

            found = discover_esp32_stream()
            if found:
                print(f"Using discovered ESP32 stream: {found}", flush=True)
                url = found
            else:
                prefix = _local_subnet_prefix() or "?"
                msg = f"Cannot reach ESP32 stream.\n"
                msg += f"  Tried: {url}\n"
                if url != original:
                    msg += f"  (you entered: {original})\n"
                msg += f"\nScanned {prefix}.x - no ESP32-CAM online.\n\n"
                msg += (
                    "ESP32 not on the network. Check:\n"
                    "  - ESP32 powered on and flashed with esp32/camera_servo/camera_servo.ino\n"
                    "  - Serial Monitor (115200): WiFi OK + Stream URL\n"
                    "  - Same Wi-Fi: Tecno pop\n"
                    "  - Browser test: open the stream URL (must show video)\n\n"
                    "Use USB robot camera instead (no ESP32 needed):\n"
                    "  python -m src.enroll --camera 1\n"
                    "  python src/vision_node.py --broker 157.173.101.159 --name Patience --camera 1"
                )
                raise RuntimeError(msg)

        print(f"Opening stream {url} (timeout {timeout_ms // 1000}s)...", flush=True)
        cap = cv2.VideoCapture()
        if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_ms)
        if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout_ms)
        opened = cap.open(url, cv2.CAP_FFMPEG)
        if not opened or not cap.isOpened():
            raise RuntimeError(f"OpenCV failed to open stream: {url}")
        return cap

    cap = _open_usb_capture(index)
    if not cap.isOpened():
        raise RuntimeError(
            f"Camera index {index} not available.\n"
            "Run: python src/vision_node.py --list-cameras\n"
            "Robot camera: --camera http://<ESP32-IP>/stream"
        )

    ok, frame = _read_usb_frame(cap)
    if not ok or frame is None:
        cap.release()
        raise RuntimeError(f"Camera {index} opened but returned no frames.")

    if _frame_is_blank(frame):
        cap.release()
        raise RuntimeError(
            f"Camera index {index} is BLANK (all black).\n\n"
            "On your PC, index 1 is often a virtual camera (e.g. EShare) — not the robot.\n"
            "The robot camera is on the ESP32 — use its stream URL instead:\n"
            "  python src/vision_node.py --camera http://<ESP32-IP>/stream --name Patience\n\n"
            "Check available USB cameras:\n"
            "  python src/vision_node.py --list-cameras"
        )

    print(
        f"USB camera {index} OK ({frame.shape[1]}x{frame.shape[0]}, "
        f"brightness={_frame_brightness(frame):.0f})",
        flush=True,
    )
    return cap


def main():
    parser = argparse.ArgumentParser(description="Test camera or ESP32 stream")
    parser.add_argument(
        "--camera",
        default=str(DEFAULT_USB_CAMERA),
        help="USB index (0=built-in, 1=external) or ESP32 URL (http://IP/stream)",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Scan LAN for ESP32-CAM if the given URL is unreachable",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available USB camera indices and exit",
    )
    args = parser.parse_args()

    if args.list:
        print("Available USB cameras:")
        print_cameras()
        return

    cap = open_camera(args.camera, auto_discover=args.discover)
    label = args.camera
    print(f"Camera test [{label}]. Press 'q' to quit.")

    while True:
        ok, frame = cap.read()
        if not ok:
            print("Failed to read frame.")
            break

        cv2.imshow("Camera Test", frame)
        if (cv2.waitKey(1) & 0xFF) == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
