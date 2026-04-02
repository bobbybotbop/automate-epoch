# FlowDesk — PyInstaller EXE parity with dev (screen capture, OCR, image matching)

The frozen **FlowDesk.exe** should behave like `python main.py` for anything that depends on **screenshots** (template image search and Tesseract OCR). Follow these conventions when changing `modules/screen.py`, the PyInstaller **spec**, or the **Build EXE** command in the UI.

## Why dev and the EXE could diverge

1. **PyScreeze / PyAutoGUI default** — On Windows, `pyautogui.locateOnScreen()` internally calls `screenshot()` with **`all_screens=False`**, so the default haystack is only the **primary monitor**, not the full virtual desktop.
2. **OCR vs image search** — If OCR used a different capture path than `locateOnScreen`, results would not line up between "find text" and "find image."
3. **PyInstaller** — One-file bundles can omit lazy-imported submodules; **`PIL.ImageGrab`** must be listed as a **hidden import** or Pillow's screen grab may fail or behave oddly in the EXE.
4. **Window-scoped OCR** — When OCR is limited to a **window rectangle**, capture quality can differ if that window is not foreground; the frozen build may need to **activate** the target window (throttled) before reading geometry and pixels.

## Single capture path in `modules/screen.py`

Keep **one** implementation for "what bitmap do we analyze?"

- **`_full_screen_capture()`** — Full-screen image. On Windows, prefer **`PIL.ImageGrab.grab(all_screens=True)`**, then fall back to **`pyautogui.screenshot(allScreens=True)`**, then **`pyautogui.screenshot()`**.
- **`_region_screenshot(region)`** — Cropped `(left, top, width, height)`. On Windows, prefer **`ImageGrab.grab(bbox=..., all_screens=True)`**, then fall back to **`pyautogui.screenshot(region=...)`**.
- **`screenshot()`** — Delegates to those helpers (full vs region).
- **`ocr_screenshot()`** — Should stay aligned with **`screenshot()`** (same pixels for OCR as for any other caller).
- **`find_image` / `find_image_box`** — Do **not** rely on **`locateOnScreen()`** alone for the haystack. Build the haystack with **`_full_screen_capture()`** and call **`pyautogui.locate(needle, haystack, confidence=...)`** so template matching uses the **same** full-screen bitmap as OCR.

## PyInstaller packaging

- **`FlowDesk.spec`** — Include **`PIL.ImageGrab`** in `hiddenimports` next to **`pytesseract`**, use **`collect_all('cv2')`** to bundle OpenCV native DLLs and data (required for PyScreeze template matching), and keep **`Tesseract-OCR`** in `datas` so the bundled `tesseract.exe` and `tessdata` resolve at runtime (see `_resource_path` / `_MEIPASS` in `screen.py`).
- **Import/Export "Build EXE"** (`ui/import_export_tab.py`) — The PyInstaller CLI should pass **`--hidden-import PIL.ImageGrab`**, **`--collect-all cv2`**, and stay in sync with the spec so builds from the UI match manual `pyinstaller FlowDesk.spec` behavior. The build also validates that `Tesseract-OCR/tesseract.exe` and `tessdata/eng.traineddata` exist before starting.
- **`Tesseract-OCR` folder** — Must contain `tesseract.exe`, all peer DLLs, and `tessdata/eng.traineddata` (at minimum). The `_configure_tesseract_cmd` function validates this at OCR init time; if the bundled copy is incomplete it falls back to a system `tesseract` on `PATH` before raising. For the bundled Windows `tesseract.exe`, set **`TESSDATA_PREFIX`** to the **`tessdata` directory** (not its parent): the binary loads `PREFIX/eng.traineddata`.

## Window title / region OCR

- **`get_window_region()`** — When **`_is_pyinstaller_bundle()`** is true ( **`sys.frozen`** or **`sys._MEIPASS`** ), optionally **activate** the matched window on a **cooldown** (see `_WINDOW_ACTIVATE_COOLDOWN_SEC`) so region screenshots match an interactive dev session when another app had focus.

## Do not "fix" in the rule file alone

- **Inactive browser tabs** still have **no painted pixels** until that tab is shown; screenshot parity does not replace a step that **switches to the correct tab**.

When adding new features that capture the screen, reuse **`screenshot` / `_full_screen_capture` / `_region_screenshot`** instead of calling **`ImageGrab`** or **`pyautogui.screenshot`** ad hoc, unless there is a documented exception.
