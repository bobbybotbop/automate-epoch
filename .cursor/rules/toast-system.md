# Toast Notification System

Source: `ui/toast.py`

## Quick usage

```python
from ui.toast import show_toast, dismiss_all, ToastType
```

## `show_toast()` signature

```python
show_toast(
    message: str,
    toast_type: ToastType = ToastType.INFO,
    duration_ms: int = 4000,
    persistent: bool = False,
    on_close: Callable[[], None] | None = None,
) -> ToastWidget
```

| Param | Purpose |
|---|---|
| `message` | Text displayed in the toast. |
| `toast_type` | Controls accent colour (see below). |
| `duration_ms` | Auto-dismiss delay. Ignored when `persistent=True`. |
| `persistent` | If `True`, the toast stays until `.dismiss()` is called or the user clicks its close button. |
| `on_close` | Callback fired when the toast is dismissed (by timer, close button, or `.dismiss()`). |

Returns the `ToastWidget` instance so you can call `.update_message()` or `.dismiss()` later.

## ToastType → colour

| Type | Accent colour | Typical use |
|---|---|---|
| `SUCCESS` | Green `(0, 220, 100)` | Operation succeeded, target found |
| `ERROR` | Red `(255, 80, 80)` | Step failed, fatal error |
| `INFO` | Blue `(100, 160, 255)` | Neutral status update |
| `WARNING` | Amber `(255, 180, 50)` | Searching, slow step, timeout approaching |

## Updating a toast in-place

```python
toast = show_toast("Searching…", ToastType.INFO, persistent=True)
# later:
toast.update_message("Found!", ToastType.SUCCESS)
```

`update_message(message, toast_type=None)` changes the text and optionally the type/colour without creating a new widget.

## Dismissing

```python
toast.dismiss()   # dismiss one toast
dismiss_all()     # dismiss every active toast
```

## Stacking

Multiple toasts stack vertically from the bottom-right corner of the screen. When one is dismissed the remaining toasts animate upward to fill the gap. The `ToastManager` singleton (module-level `_manager`) handles positioning automatically.

## Layout constants

| Constant | Value | Meaning |
|---|---|---|
| `TOAST_W` | 320 px | Widget width |
| `TOAST_H` | 56 px | Widget height |
| `ACCENT_W` | 5 px | Left colour strip width |
| `MARGIN` | 12 px | Gap between toasts / screen edge |
| `ANIM_MS` | 250 ms | Slide-in animation duration |

## Window flags

Each toast uses `FramelessWindowHint | WindowStaysOnTopHint | Tool` with `WA_TranslucentBackground` and `WA_ShowWithoutActivating`, so it floats above all windows without stealing focus and remains visible even when FlowDesk is minimized.
