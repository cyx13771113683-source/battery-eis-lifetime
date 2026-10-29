"""Direct Linux input-event reader used by the MPI5006 smoke test."""

from dataclasses import dataclass
import os
from pathlib import Path
import select
import struct

try:
    import fcntl
except ImportError:  # Host-side unit tests run on Windows; board runtime is Linux.
    fcntl = None


INPUT_EVENT_FORMAT = "@llHHi"
INPUT_EVENT_SIZE = struct.calcsize(INPUT_EVENT_FORMAT)
EV_SYN = 0
EV_KEY = 1
EV_ABS = 3
BTN_TOUCH = 330
SYN_REPORT = 0
ABS_X = 0
ABS_Y = 1
ABS_MT_TRACKING_ID = 57
ABS_MT_POSITION_X = 53
ABS_MT_POSITION_Y = 54


@dataclass(frozen=True)
class InputEvent:
    seconds: int
    microseconds: int
    type: int
    code: int
    value: int


@dataclass(frozen=True)
class TouchDevice:
    path: Path
    name: str


def decode_event(data):
    if len(data) != INPUT_EVENT_SIZE:
        raise ValueError(f"input event requires {INPUT_EVENT_SIZE} bytes")
    return InputEvent(*struct.unpack(INPUT_EVENT_FORMAT, data))


def scale_axis(value, minimum, maximum, pixels):
    if pixels <= 0 or maximum <= minimum:
        raise ValueError("invalid axis range")
    value = min(maximum, max(minimum, int(value)))
    return int(round((value - minimum) * (pixels - 1) / (maximum - minimum)))


def discover_touch_devices(sys_class=Path("/sys/class/input"), dev_input=Path("/dev/input")):
    devices = []
    root = Path(sys_class)
    for event_dir in sorted(root.glob("event*")):
        try:
            name = (event_dir / "device" / "name").read_text(encoding="utf-8", errors="replace").strip()
            ev_mask_text = (event_dir / "device" / "capabilities" / "ev").read_text(encoding="ascii").strip()
            ev_mask = int(ev_mask_text.replace(" ", ""), 16)
        except (OSError, ValueError):
            continue
        if not ev_mask & (1 << EV_ABS):
            continue
        score = 0 if any(word in name.lower() for word in ("touch", "ft5", "goodix", "hid")) else 1
        devices.append((score, TouchDevice(Path(dev_input) / event_dir.name, name)))
    return [device for _, device in sorted(devices, key=lambda item: (item[0], str(item[1].path)))]


def _eviocgabs(code):
    # Linux _IOR('E', 0x40 + code, struct input_absinfo[24]).
    return 0x80184540 + int(code)


def _axis_range(fd, preferred, fallback):
    if fcntl is None:
        return fallback
    for code in preferred:
        buffer = bytearray(24)
        try:
            fcntl.ioctl(fd, _eviocgabs(code), buffer, True)
            _, minimum, maximum, _, _, _ = struct.unpack("@iiiiii", buffer)
            if maximum > minimum:
                return code, minimum, maximum
        except OSError:
            pass
    return fallback


class TouchReader:
    def __init__(self, device, width=800, height=600):
        self.device = device
        self.width = int(width)
        self.height = int(height)
        self.fd = os.open(str(device.path), os.O_RDONLY | os.O_NONBLOCK)
        self.x_code, self.x_min, self.x_max = _axis_range(
            self.fd, (ABS_MT_POSITION_X, ABS_X), (ABS_X, 0, 4095)
        )
        self.y_code, self.y_min, self.y_max = _axis_range(
            self.fd, (ABS_MT_POSITION_Y, ABS_Y), (ABS_Y, 0, 4095)
        )
        self._raw_x = self.x_min
        self._raw_y = self.y_min
        self.point = None
        self._dirty = False
        self.contact_known = False
        self.is_down = False
        self.just_pressed = False
        self.just_released = False
        self._press_pending = False
        self._contact_x_seen = False
        self._contact_y_seen = False
        self._report_x_seen = False
        self._report_y_seen = False
        self._x_known = False
        self._y_known = False
        # QDtech MPI5001/5006 firmware appends calibration and axis-maximum
        # pairs to each report.  The first X/Y pair is the actual contact.
        self._first_axis_pair_per_report = "mpi500" in device.name.lower()

    @classmethod
    def autodetect(cls, width=800, height=600):
        errors = []
        for device in discover_touch_devices():
            try:
                return cls(device, width, height)
            except OSError as exc:
                errors.append(f"{device.path}: {exc}")
        if errors:
            raise OSError("; ".join(errors))
        return None

    def _set_contact_state(self, is_down):
        is_down = bool(is_down)
        was_known = self.contact_known
        was_down = self.is_down
        self.contact_known = True
        self.is_down = is_down
        if was_down == is_down:
            return not was_known
        if is_down:
            self.just_pressed = True
            self._press_pending = True
            self._contact_x_seen = self._report_x_seen
            self._contact_y_seen = self._report_y_seen
            self.point = None
        else:
            self.just_released = True
            self._press_pending = False
        return True

    def _consume_event(self, event):
        if event.type == EV_ABS and event.code == self.x_code:
            if (getattr(self, "_first_axis_pair_per_report", False)
                    and self._report_x_seen):
                return False
            self._raw_x = event.value
            self._x_known = True
            self._report_x_seen = True
            if self.is_down:
                self._contact_x_seen = True
            self._dirty = True
            return True
        if event.type == EV_ABS and event.code == self.y_code:
            if (getattr(self, "_first_axis_pair_per_report", False)
                    and self._report_y_seen):
                return False
            self._raw_y = event.value
            self._y_known = True
            self._report_y_seen = True
            if self.is_down:
                self._contact_y_seen = True
            self._dirty = True
            return True
        if event.type == EV_KEY and event.code == BTN_TOUCH:
            changed = self._set_contact_state(event.value != 0)
            self._dirty = self._dirty or changed
            return changed
        if event.type == EV_ABS and event.code == ABS_MT_TRACKING_ID:
            changed = self._set_contact_state(event.value >= 0)
            self._dirty = self._dirty or changed
            return changed
        return False

    def poll(self, timeout=0.0):
        self.just_pressed = False
        self.just_released = False
        ready, _, _ = select.select([self.fd], [], [], max(0.0, float(timeout)))
        if not ready:
            return self.point, False
        while True:
            try:
                data = os.read(self.fd, INPUT_EVENT_SIZE)
            except BlockingIOError:
                break
            if not data:
                break
            if len(data) != INPUT_EVENT_SIZE:
                continue
            event = decode_event(data)
            if event.type == EV_SYN and event.code == SYN_REPORT and self._dirty:
                if self._press_pending:
                    if self._contact_x_seen and self._contact_y_seen:
                        self.just_pressed = True
                        self._press_pending = False
                    else:
                        self.just_pressed = False
                        self.point = None
                if not self._press_pending and self._x_known and self._y_known:
                    self.point = (
                        scale_axis(self._raw_x, self.x_min, self.x_max, self.width),
                        scale_axis(self._raw_y, self.y_min, self.y_max, self.height),
                    )
                elif not self._press_pending:
                    self.point = None
                self._dirty = False
                self._report_x_seen = False
                self._report_y_seen = False
                return self.point, True
            if event.type == EV_SYN and event.code == SYN_REPORT:
                self._report_x_seen = False
                self._report_y_seen = False
            self._consume_event(event)
        return self.point, False

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
