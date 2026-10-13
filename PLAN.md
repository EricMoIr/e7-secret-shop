# E7 Secret Shop Clicker — Implementation Plan

## 1. Goal

A Windows desktop app that, when started:

1. Confirms `EpicSeven.exe` is running and brings its window to the foreground.
2. Uses OCR (Tesseract) to confirm the game is on the **Secret Shop** screen, and stops with a prompt if it isn't.
3. Reads the starting **skystones** and **gold** with OCR, **once**. From then on it tracks both through a ledger of everything it spends.
4. Runs a loop, either N times or until it runs out of skystones or gold:
   - Scans the configured shop slots on page 1, scrolls down with the mouse wheel, and scans the configured slots on page 2.
   - Buys every **Covenant Bookmark** and **Mystic Medal** it finds.
   - Checks the ledger, then refreshes the shop (3 skystones).
5. Moves the mouse like a person: an arc to a random point inside each target rectangle, with random delays.

The app must always run as administrator. A configuration screen for defining coordinates comes in a later phase. Until then, coordinates come from a hand-edited JSON file.

**Guiding rule:** a wanted item that is found is a priority. The app never silently skips one. If it can't buy a wanted item (the buy dialog doesn't open, or there isn't enough gold), it **stops the run**.

## 2. Platform & Tech Stack

The game is a Windows process, so the app must run on **native Windows Python, not inside WSL**.

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.12 (Windows) | |
| Process detection | `psutil` | Find a process named `EpicSeven.exe` and get its PID |
| Window focus / geometry | `pywin32` (`win32gui`, `win32process`) | Map PID → HWND, `SetForegroundWindow`, `GetClientRect` + `ClientToScreen` |
| Screenshot | `mss` | Capture the game's client rect or a sub-region |
| OCR | `pytesseract` + Tesseract-OCR binary (UB Mannheim build) | Text mode for labels and keywords, single-line mode for currencies |
| Image preprocessing | `Pillow` (+ optional `opencv-python`) | Grayscale, upscale ×2, threshold |
| Mouse input | Win32 `SendInput` via `ctypes` (`win_input.py`) | Absolute moves, clicks and wheel through the same path as hardware input; no extra dependency |
| Abort hotkey | `keyboard` or `pynput` | Global hotkey (e.g. `F12`) stops the run immediately |
| GUI | `PySide6` | Start/Stop, run-mode selection, live stats, config screen later |
| Config validation | `pydantic` | Clear errors for a malformed JSON |
| Packaging | `PyInstaller` with `--uac-admin` | Single `.exe` whose manifest requires administrator rights (§3.7) |

## 3. Key Design Decisions

### 3.1 Coordinates are window-relative
Store all rectangles **relative to the game window's client area**. At runtime, add the client-area origin. Store the reference `window_size` with the coordinates. If the current client size differs, abort with a clear message (v1; proportional scaling later).

Call `SetProcessDpiAwareness(2)` (per-monitor DPI aware) at startup so Windows display scaling doesn't skew coordinates.

### 3.2 Rectangles
Every target is `[x0, y0, x1, y1]`: the top-left and bottom-right corners of an axis-aligned rectangle.

- **Item slots** have their own rectangles per page. Each slot rectangle is used for two things: the OCR region that reads the item name, and the click target.
- **Page 2's config lists only the slots that page 1 didn't already cover.** After scrolling, some page-1 items are still on screen, and the config leaves them out of page 2.
- **Dialogs** (the buy confirmation and the refresh confirmation) always appear in the same place. Each has one fixed rectangle for its OCR check and one for its button.

### 3.3 Human-like movement
- **Path:** a quadratic or cubic Bézier curve from the current cursor position to the target point. Offset the control point(s) perpendicular to the straight line by a random 10–30% of the distance, on a random side.
- **Target point:** a random point inside the rectangle, biased toward the center with a clipped Gaussian.
- **Timing:** duration scales with distance (about 150–450 ms), with ease-in-out speed and a few px of jitter.
- **Delays:** `random.uniform(0.100, 0.200)` seconds before and after each click.
- **Scrolling:** move the cursor (with an arc) to a point inside the configured scroll area, then send N wheel notches, with a short random pause between notches.

### 3.4 OCR

All text comparisons are **case-insensitive substring checks**: lower-case the OCR output and test `keyword in text`.

| Check | Region | Keyword / mode |
|---|---|---|
| Secret Shop screen | full window or a header region | `"secret shop"` |
| Item identification | the slot's rectangle (or a name sub-region of it) | `"covenant"` or `"mystic"` |
| Buy dialog opened | the buy dialog region | `"buy"` |
| Refresh dialog opened | the refresh dialog region | `"confirm"` |
| Starting skystones / gold | small fixed regions in the top bar | number read (below), **once at startup** |

- **Items are identified only by their keyword** (`"covenant"` / `"mystic"`). The prices printed in the shop are **never read or compared**. Prices come only from the config.
- **Reading a number:** don't try to interpret separators. Run OCR on the region in single-line mode (`--psm 7`), then **remove every non-digit character** (`re.sub(r"\D", "", text)`) and convert the rest with `int()`. Commas, periods, spaces, and stray punctuation all disappear, so `"1,234,567"`, `"1.234.567"`, and `"1 234,567"` all become `1234567`. An empty result counts as a failed read.
- **Consensus read:** the starting values are the only currency reads in the whole run, so a misread affects every later decision. Read each value 3 times and accept it only when all three agree. Otherwise, stop with "couldn't read gold/skystones" and save a debug capture.
- **Confirm before start:** the GUI shows the starting skystones and gold it read. The user confirms them, or corrects them by hand, before pressing Start.
- Retry each text check up to N times (e.g. 3 × 300 ms) because popups animate in.
- **Debug captures:** when an OCR check fails, save the image and the raw OCR text to a `debug/` folder.

### 3.5 Ledger
Nothing else spends or earns gold or skystones during a run. So the app reads the starting values once, records every spend in a **ledger**, and works out the current balances from it:

```python
@dataclass
class LedgerEntry:
    timestamp: datetime
    round: int              # refresh cycle, 1-based
    kind: Literal["purchase", "refresh"]
    item: str | None        # "Covenant Bookmarks" / "Mystic Medals"; None for refresh
    page: int | None
    slot: int | None
    gold: int               # gold spent (0 for refresh)
    skystones: int          # skystones spent (0 for purchase)

class Ledger:
    initial_gold: int
    initial_skystones: int
    entries: list[LedgerEntry]

    gold_spent / skystones_spent      -> sums over entries
    gold_remaining      = initial_gold - gold_spent
    skystones_remaining = initial_skystones - skystones_spent
    count(item) / refreshes           -> for the summary
```

- The ledger is the single source of truth for stop conditions, the live counters in the GUI, the end-of-run summary, and later reporting.
- After every entry, append it to a JSON Lines file (`runs/<timestamp>.jsonl`). If the app crashes, the record of what was bought survives, and reporting can just read those files later.
- Skystones use the same ledger. Each refresh is recorded as an entry costing `refresh_cost` (3).

### 3.6 Run modes and stopping conditions
At startup, with the confirmed starting values:

```
max_refreshes = floor(initial_skystones / 3)
gold_threshold = max(item.price for item in wanted_items)   # 280,000 (Mystic Medals)
```

The GUI offers two modes:
- **Repeat N times:** the user enters N, capped at `max_refreshes`.
- **Until out of skystones:** N = `max_refreshes`.

The run stops at whichever comes first:

| Condition | When checked | Kind |
|---|---|---|
| `initial_gold < gold_threshold` | at startup | refuse to start — not enough gold |
| N refreshes done | before each refresh | normal end |
| `ledger.skystones_remaining < refresh_cost` | before each refresh | normal end — out of skystones |
| `ledger.gold_remaining < gold_threshold` | before each refresh | normal end — out of gold |
| A wanted item is found but `ledger.gold_remaining < item.price` | before each purchase | normal end — out of gold |
| Buy dialog doesn't show `"buy"` after clicking a wanted item | after the click | **error stop** |
| Refresh dialog doesn't show `"confirm"` | after the click | **error stop** |
| Game window loses focus | before every click | **error stop** |
| User presses the abort hotkey or Stop | anytime | user stop |

**Why the pre-purchase check is still needed:** the threshold check guarantees ≥ 280,000 gold at the start of a round, but several purchases in the same round can drop gold below a later item's price. For example: 300,000 → buy Covenant Bookmarks (184,000) → 116,000 left → Mystic Medals found. This check stops the run there instead of trying a purchase that would fail.

Every stop shows its reason in the GUI next to the run summary, and records it in the run file.

### 3.7 Administrator rights
- **Packaged build:** run `pyinstaller --uac-admin`. It embeds `requestedExecutionLevel level="requireAdministrator"` in the exe manifest, so Windows shows the UAC prompt every time it starts. Declining the prompt means the app doesn't start.
- **Running from source (development):** at startup, check `ctypes.windll.shell32.IsUserAnAdmin()`. If it returns false, relaunch the app with `ShellExecuteW(None, "runas", sys.executable, args, ...)` and exit. Both entry points therefore always run elevated.
- Running as admin also ensures clicks reach the game if the game runs elevated (Windows UIPI blocks input from lower-integrity processes).

### 3.8 Safety
- Global abort hotkey, checked between every step and during mouse movement.
- Before every click, check that the game is still the foreground window. Abort if it isn't.
- **Dry-run mode:** runs every OCR check and cursor movement without clicking or scrolling. It records what it *would* buy in a ledger marked `dry_run`.
- A log file with every step and OCR result. The ledger file records spends separately.
- A run summary built from the ledger: refreshes done, items bought by type, skystones and gold spent and remaining, and why the run stopped.

## 4. Config File (v1 draft)

```json
{
  "tesseract_path": "C:\\Program Files\\Tesseract-OCR\\tesseract.exe",
  "process_name": "EpicSeven.exe",
  "window_size": { "width": 1920, "height": 1080 },
  "abort_hotkey": "f12",

  "ocr": {
    "secret_shop_text": "secret shop",
    "buy_text": "buy",
    "confirm_text": "confirm",
    "retries": 3,
    "retry_delay_ms": 300,
    "number_consensus_reads": 3
  },

  "currency": {
    "skystones_region": [1400, 20, 1550, 60],
    "gold_region": [1600, 20, 1800, 60],
    "refresh_cost": 3
  },

  "wanted_items": [
    { "name": "Covenant Bookmarks", "keyword": "covenant", "price_gold": 184000 },
    { "name": "Mystic Medals",      "keyword": "mystic",   "price_gold": 280000 }
  ],

  "pages": [
    {
      "slots": [
        { "target": [1100, 200, 1700, 290] },
        { "target": [1100, 310, 1700, 400] },
        { "target": [1100, 420, 1700, 510] },
        { "target": [1100, 530, 1700, 620] }
      ]
    },
    {
      "slots": [
        { "target": [1100, 480, 1700, 570] },
        { "target": [1100, 590, 1700, 680] }
      ]
    }
  ],

  "scroll": { "area": [1100, 300, 1700, 600], "notches": -5, "notch_delay_ms": [30, 80] },

  "buy_dialog":     { "ocr_region": [600, 300, 1300, 750], "button": [900, 650, 1100, 710] },
  "refresh_button": [100, 950, 350, 1010],
  "refresh_dialog": { "ocr_region": [600, 300, 1300, 750], "button": [900, 650, 1100, 710] },

  "runs_dir": "runs"
}
```

The coordinates are placeholders. A slot can optionally have a separate `name_region` if the clickable area and the item-name text are in different places. `name` is only for display and the ledger; matching uses `keyword`.

## 5. Execution Flow

```
startup
 ├─ ensure admin (relaunch with "runas" when running from source)
 ├─ find EpicSeven.exe ── not found → "Game not running", stop
 ├─ focus window, verify client size
 ├─ OCR "secret shop" ── not found → prompt "Go to the Secret Shop", stop
 ├─ read skystones, gold (digits only, 3-read consensus) ── no consensus → stop
 ├─ user confirms/corrects values → ledger = Ledger(initial_gold, initial_skystones)
 ├─ initial_gold < gold_threshold → "Not enough gold", stop
 └─ user picks mode (N or until out of skystones) → Start

round = 1
loop:
   process_page(pages[0])          # list is at the top
   scroll down (wheel)
   process_page(pages[1])          # only slots not covered by page 1

   # pre-refresh check, from the ledger only
   if round > N                                  → stop "done"
   if ledger.skystones_remaining < refresh_cost  → stop "out of skystones"
   if ledger.gold_remaining < gold_threshold     → stop "out of gold"

   click_target(refresh_button)
   OCR refresh_dialog for "confirm" ── not found → error stop
   click_target(refresh_dialog.button)
   ledger.add(refresh, skystones=refresh_cost)
   wait for the list to reload (it returns to the top)
   round += 1
show summary (from ledger) + stop reason

process_page(page):
   for slot in page.slots:
      text = lower(OCR(slot.name_region or slot.target))
      item = first wanted_item whose keyword is in text ── none → continue
      if ledger.gold_remaining < item.price → stop "out of gold"
      click_target(slot.target)
      OCR buy_dialog for "buy" ── not found → error stop (save debug capture)
      click_target(buy_dialog.button)
      ledger.add(purchase, item, page, slot, gold=item.price)

click_target(rect):
   point = random point in rect (window → screen coords)
   arc_move(current_pos → point)
   sleep U(100, 200) ms
   check abort flag + foreground window
   click
   sleep U(100, 200) ms
```

- **Round count:** N counts refreshes. The shop is scanned N + 1 times: the shop as it is at the start, plus once after each of the N refreshes. The final scan uses no skystones.
- **Stops:** a `StopRun(reason, is_error)` exception raised from any depth and caught in `runner.py`. The runner writes the stop reason to the ledger file and builds the summary.

## 6. Project Structure

```
e7-secret-shop/
├─ pyproject.toml
├─ config.example.json
├─ build.spec               # PyInstaller spec with uac_admin=True
├─ src/e7shop/
│  ├─ __main__.py           # entry point: ensure admin, launch GUI
│  ├─ elevation.py          # IsUserAnAdmin + runas relaunch
│  ├─ config.py             # pydantic models, load/validate JSON
│  ├─ window.py             # process lookup, HWND, focus, client rect, DPI awareness
│  ├─ capture.py            # mss screenshot of client area / sub-region
│  ├─ ocr.py                # preprocess, contains_text, read_number (digits only, consensus)
│  ├─ mouse.py              # bezier arc, random point in rect, click, wheel scroll
│  ├─ ledger.py             # LedgerEntry, Ledger, JSONL persistence, summary
│  ├─ shop.py               # process_page, buy_item, refresh
│  ├─ runner.py             # main loop, stop conditions, StopRun handling; worker thread
│  ├─ abort.py              # global hotkey → threading.Event
│  └─ gui/
│     ├─ main_window.py     # value confirmation, mode selection, Start/Stop/Dry-run, stats, log
│     └─ config_editor.py   # (phase 3)
└─ tests/
   ├─ test_mouse.py         # path endpoints, points stay in rect, curve bounded
   ├─ test_config.py        # valid/invalid configs
   ├─ test_ocr.py           # sample screenshots + read_number on "1,234,567", "1.234.567", "1 234,567", ""
   ├─ test_ledger.py        # balances, counts, JSONL round-trip
   └─ test_runner.py        # every stop condition in §3.6 with a fake shop (no game needed)
```

`runner.py` talks to the game only through small interfaces (`Screen`, `Mouse`). The loop and stop-condition logic can then be unit-tested with fakes.

## 7. Phases

**Phase 1: core engine (CLI)**
1. Scaffold the project, dependencies, `config.example.json`, and the admin check / relaunch. **Done.**
2. `window.py`: detect the process, focus the window, get the client rect. **Done.**
3. Collect sample screenshots: the Secret Shop with each wanted item visible (top and scrolled), the top bar with currencies, the buy dialog, and the refresh dialog.
4. `capture.py` + `ocr.py`: tune preprocessing until the keywords, dialog text, and currency numbers are read reliably from the samples.
5. `ledger.py`: ledger model, JSONL persistence, and summary. **Done.**
6. `mouse.py`: Bézier arc movement, clicks, and wheel scrolling, plus unit tests. **Done.**
7. `shop.py` + `runner.py` + `abort.py`: full loop from the command line (`python -m e7shop run --config cfg.json --count N|--until-empty [--dry-run]`), plus `python -m e7shop inspect` to capture and OCR every configured region. The CLI prints the starting values it read and asks for confirmation. **Done.**
8. Test against the game: dry runs first, then a live run with N = 1.

**Phase 2: GUI**
9. PySide6 main window:
   - config file picker
   - the starting skystones / gold it read, editable, with a Confirm step
   - the computed max refreshes
   - mode selector (N spinner capped at the max, or "until out of skystones")
   - Start / Stop / Dry-run buttons
   - live counters from the ledger (refreshes, bookmarks, medals, gold and skystones remaining) and a log view
   - message boxes for "game not running", "go to Secret Shop", "not enough gold", and the stop reason

   The runner runs on a `QThread`.

**Phase 3: configuration screen (to be defined)**
10. Possible approach: capture a screenshot of the game, show it in the editor, and let the user drag rectangles for slots (per page), dialogs, currency regions, and the scroll area. Details to be decided later.

**Phase 4: packaging**
11. Build with PyInstaller `--uac-admin --onefile --windowed`. On first run, check that Tesseract is installed and the configured path exists. Confirm that the UAC prompt appears and that a non-elevated launch is impossible.

**Later: reporting**
12. Read the `runs/*.jsonl` files: totals per run and over time, gold per bookmark/medal, and skystones per item.

## 8. Risks

- **OCR reliability:** stylized game fonts can mislead Tesseract. Mitigations: short keyword matching, tight regions, preprocessing, retries, and debug captures.
- **Starting currency misread:** every later decision depends on the two starting reads. Mitigations: keeping digits only, 3-read consensus, and the user confirming the values before starting. A dropped or extra digit is still possible if OCR is consistently wrong, and the confirmation step catches that.
- **Network latency after confirming (known limitation, to tackle later):** the shop belongs only to this user, so nothing else can buy an item or refresh the shop during a run. The risk is latency instead. After the app clicks the button in the buy or refresh dialog, the game may not finish the action straight away, because it waits for the server. v1 records the spend as soon as it clicks and goes on to the next step after the usual 100–200 ms delay. If the game is slow, problems can follow:
  - the next click lands while a loading or dialog screen is still showing
  - the next slot is read before the list updates
  - after a refresh, the old items are scanned again
  - the record of spending gets out of step with the game

  The current checks partly guard against this: each dialog's text is checked with retries, and a missing "buy" or "confirm" stops the run. They don't confirm that the action finished. **Future fix:** after each confirm click, wait for the result before continuing (e.g. the dialog has closed, the slot shows "sold out", or the list contents have changed after a refresh), with a timeout. Record the spend only once the result is seen, and stop the run if the timeout passes.
- **UI timing:** popups and the refresh animation take time. The OCR retry loop handles this instead of fixed sleeps.
