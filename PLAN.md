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

The app must always run as administrator. Coordinates come from a first-run setup wizard (§9): the user marks regions on screenshots of the game, and the wizard writes the config file.

**Guiding rule:** a wanted item that is found is a priority. The app never silently skips one. If it can't buy a wanted item (the buy dialog doesn't open after 3 clicks, or there isn't enough gold), it **stops the run**. An item whose name can't be read at all is skipped, with a warning, a debug capture and a ledger entry (§3.6).

## 2. Platform & Tech Stack

The game is a Windows process, so the app must run on **native Windows Python, not inside WSL**.

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.12 (Windows) | |
| Process detection | `psutil` | Find a process named `EpicSeven.exe` and get its PID |
| Window focus / geometry | `pywin32` (`win32gui`, `win32process`) | Map PID → HWND, `SetForegroundWindow`, `GetClientRect` + `ClientToScreen` |
| Screenshot | `mss` | Capture the game's client rect or a sub-region |
| OCR | `pytesseract` + Tesseract-OCR binary (UB Mannheim build, bundled in the exe) | Text mode for labels, single-line mode for numbers (currencies) |
| Image preprocessing | `Pillow` (+ optional `opencv-python`) | Grayscale, upscale ×2, threshold |
| Mouse input | Win32 `SendInput` via `ctypes` (`win_input.py`) | Absolute moves, clicks and wheel through the same path as hardware input; no extra dependency |
| Abort hotkey | `keyboard` or `pynput` | Global hotkey (e.g. `F12`) stops the run immediately |
| GUI | `PySide6` | Start/Stop, run-mode selection, live stats, config screen later |
| Config validation | `pydantic` | Clear errors for a malformed JSON |
| Packaging | `PyInstaller` with `--uac-admin` | Single `.exe` whose manifest requires administrator rights (§3.7) |

## 3. Key Design Decisions

### 3.1 Coordinates are window-relative
Store all rectangles **relative to the game window's client area**. At runtime, add the client-area origin. Store the reference `window_size` with the coordinates. **Regions are scaled to the window's current size** (`WindowsGame._to_screen`, read on every call): the game keeps its window 16:9 and scales its contents with it. A resize test read every region correctly from 0.6x to 1.4x (746×420 to 1740×979), and the game snapped odd shapes back to 16:9. A run only refuses to start when the window's shape differs from `window_size` by more than 2% (`WindowSizeMismatch`, which offers setup), and logs a warning below 0.6x.

Call `SetProcessDpiAwareness(2)` (per-monitor DPI aware) at startup so Windows display scaling doesn't skew coordinates.

### 3.2 Rectangles
Every target is `[x0, y0, x1, y1]`: the top-left and bottom-right corners of an axis-aligned rectangle.

- **Item slots** have their own rectangles per page. Each slot has two: the **name region** (OCR'd to identify the item, §3.4) and the **buy button** (the click target). All rows share the same layout and are evenly spaced, so setup measures one row per page and derives the rest (§9.5).
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
| Item identification | the slot's name region | `keyword` of a wanted item (e.g. `"covenant"`, `"mystic"`) |
| Buy dialog opened | the buy dialog region | `"buy"` |
| Refresh dialog opened | the refresh dialog region | `"confirm"` |
| Starting skystones / gold | small fixed regions in the top bar | number read (below), **once at startup** |

- **Items are identified by their name.** Each slot's item name is read as text and checked for each wanted item's `keyword` (case-insensitive substring). Keywords must be unique (config validation). `price_gold` is only what the item costs, for the ledger and the gold checks; prices aren't read from the shop.
- **A name that can't be read is recovered, then skipped.** Each slot's name is retried like any other check. If no text comes back, the app first checks whether a dialog is covering the list (§3.6); if not, it waits for the list to stop moving and reads again. If it still gets nothing, it skips the slot with a warning, a debug capture and a `skip` ledger entry.
- **History:** phase 3 first identified items by price, read as a number. On the real shop that needed digits-only boxes (the coin icon's "+" read as a digit) and a near-miss check that flagged many real prices one digit away from a wanted one (18,000, 240,000, 380,000, 480,000…). Names are long words that OCR reads more reliably, so matching went back to names.
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
| Buy dialog doesn't open after 3 clicks on a wanted item's buy button (see recovery below) | after each click | **error stop** |
| Refresh dialog doesn't open after 3 clicks on Refresh | after each click | **error stop** |
| Game window loses focus | before every click | **error stop** |
| User presses the abort hotkey or Stop | anytime | user stop |

**Why the pre-purchase check is still needed:** the threshold check guarantees ≥ 280,000 gold at the start of a round, but several purchases in the same round can drop gold below a later item's price. For example: 300,000 → buy Covenant Bookmarks (184,000) → 116,000 left → Mystic Medals found. This check stops the run there instead of trying a purchase that would fail.

Every stop shows its reason in the GUI next to the run summary, and records it in the run file.

**Recovering instead of stopping** (added after the first live runs):
- **Dialog not recognised after clicking a buy or refresh button.** The button is grabbed before the click and compared after (mean grayscale difference < 12 = unchanged). Unchanged: the click didn't register, so click again, up to 3 clicks in total. Changed: something covers the button, and that can only be the dialog (an open dialog dims the whole shop), so click its confirm button. Risk: if the game ever shows a hover effect on the button, "unchanged" could read as "covered"; the confirm click would then land on the shop list. A dry run with a wanted item on screen shows which branch was taken.
- **Item name not read.** Check the buy dialog region for "buy", then the refresh dialog region for "confirm". If one is open, its confirm click from the previous step didn't land: click it again to finish that step (it's already in the ledger, so nothing is recorded twice), then read the name again. If neither is open, wait for the list to stop moving and read again. If it still doesn't read, skip the slot with a `skip` ledger entry; the summary counts them.

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
    { "name": "Covenant Bookmarks", "price_gold": 184000 },
    { "name": "Mystic Medals",      "price_gold": 280000 }
  ],

  "pages": [
    {
      "slots": [
        { "name_region": [1200, 230, 1420, 270], "buy_button": [1440, 220, 1690, 280] },
        { "name_region": [1200, 340, 1420, 380], "buy_button": [1440, 330, 1690, 390] },
        { "name_region": [1200, 450, 1420, 490], "buy_button": [1440, 440, 1690, 500] },
        { "name_region": [1200, 560, 1420, 600], "buy_button": [1440, 550, 1690, 610] }
      ]
    },
    {
      "slots": [
        { "name_region": [1200, 510, 1420, 550], "buy_button": [1440, 500, 1690, 560] },
        { "name_region": [1200, 620, 1420, 660], "buy_button": [1440, 610, 1690, 670] }
      ]
    }
  ],

  "scroll": { "area": [1100, 200, 1700, 620], "notches": -10, "notch_delay_ms": [30, 80] },

  "buy_dialog":     { "ocr_region": [600, 300, 1300, 750], "button": [900, 650, 1100, 710] },
  "refresh_button": [100, 950, 350, 1010],
  "refresh_dialog": { "ocr_region": [600, 300, 1300, 750], "button": [900, 650, 1100, 710] },

  "runs_dir": "runs"
}
```

The coordinates are placeholders. The setup wizard (§9) writes them, along with `window_size` and `scroll.area`. `name` is only for display and the ledger; matching uses `keyword`. Configs in older formats (slots with `target` or `price_region`) fail validation, and the GUI offers to run setup (§9.1), which keeps the wanted items and gives any without a keyword the first word of their name.

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

   click_through_dialog(refresh_button, refresh_dialog)
   ledger.add(refresh, skystones=refresh_cost)
   wait for the list to reload (it returns to the top)
   round += 1
show summary (from ledger) + stop reason

process_page(page):
   for slot in page.slots:
      text = lower(OCR(slot.name_region)), retried
         ── no text → buy or refresh dialog still open? confirm it (finish that step) : wait until the list is still
                      read again ── still no text → skip the slot (warn, debug capture, ledger "skip")
      item = first wanted_item whose keyword is in text ── none → continue
      if ledger.gold_remaining < item.price → stop "out of gold"
      click_through_dialog(slot.buy_button, buy_dialog)
      ledger.add(purchase, item, page, slot, gold=item.price)

click_through_dialog(button, dialog):
   before = grab(button)
   up to 3 times:
      click_target(button)
      OCR dialog for its keyword, retried ── found → click_target(dialog.button), done
      button looks like `before` (mean diff < 12)? the click didn't register → next try
      otherwise the dialog covers it (unread) → click_target(dialog.button), done
   ── 3 clicks without a dialog → error stop

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
│  ├─ calibrate.py          # (phase 3) row detection, page-2 rows, scroll area, build config — no Qt, no Win32
│  ├─ setup_session.py      # (phase 3) find/focus/capture/scroll/OCR for the wizard; Windows impl + protocol
│  └─ gui/
│     ├─ main_window.py     # value confirmation, mode selection, Start/Stop/Dry-run, stats, log
│     ├─ region_picker.py   # (phase 3) screenshot view: zoom, pan, drag to mark rectangles
│     └─ setup_wizard.py    # (phase 3) first-run setup QWizard
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

   The runner runs on a `QThread`. **Done.**

**Phase 3: first-run setup (§9)**
10. Price-based identification (later replaced by names again, see §3.4): the config model (`price_region` / `buy_button` slots, wanted items without `keyword`, unique prices), `shop.py` (number read, error stop on no digits, near-miss warning), `tests/fakes.py`, the tests, `config.example.json` and the README. **Done.**
11. `calibrate.py` + `test_calibrate.py`: row detection, page-2 rows, scroll area, building the config. **Done**, except tuning the match threshold on real screenshots (needs the phase 1 step 3 samples).
12. `setup_session.py`: Windows capture/scroll/OCR session and a fake for tests. **Done.**
13. `gui/region_picker.py` + tests. **Done.**
14. `gui/setup_wizard.py` + tests, walked through end to end with the fake session. **Done.**
15. Boot flow and main window: open setup when there's no config, offer it when the config is invalid, a **Run setup…** button, and a **Run setup** button on the window-size message. **Done.**
16. Test against the game: run setup, run `inspect`, do a dry run. Then resize the window and check that the run refuses to start and offers setup.

**Phase 4: packaging**
17. Build with PyInstaller `--uac-admin --onefile --windowed`. On first run, check that Tesseract is installed and the configured path exists. Confirm that the UAC prompt appears and that a non-elevated launch is impossible.
    - `build.spec` + `packaging/launcher.py`; `scripts/build.ps1` (Windows) and `scripts/build-from-wsl.sh` (copies the tree to a Windows folder and builds there).
    - The build first makes a console variant of the same bundle without the admin manifest and runs its `--self-test` (`selftest.py`), so a broken bundle fails the build instead of failing silently after the UAC prompt.
    - Frozen-app behaviour: `config.json` defaults to the exe's folder (`default_config_path()`); the exe is GUI-only and says so if given a command; unexpected errors show a dialog and go to the log instead of vanishing.
    - **Tesseract is bundled** (`scripts/fetch_tesseract.py`): the pinned UB Mannheim installer is downloaded with its SHA-256 checked and unpacked with 7-Zip, never run. Only `tesseract.exe`, the DLLs it loads (followed through PE imports: 26 of the 51), `eng.traineddata` and the licences are kept, and the DLLs are stripped of debug info where a PE-capable `strip` exists (121 MB → 23 MB). The spec bundles `vendor/tesseract/`; `ocr.find_tesseract()` picks a configured `tesseract_path` if it exists, then the bundled copy, then the standard install. `tesseract_path` is now optional, and setup only writes it when the user picked one (no bundled copy).
    - The self-test also reads a rendered price and text with the bundled Tesseract, so a bundle whose Tesseract can't load its DLLs or language data fails the build.

**Later: reporting**
18. Read the `runs/*.jsonl` files: totals per run and over time, gold per bookmark/medal, and skystones per item.

## 8. Risks

- **OCR reliability:** stylized game fonts can mislead Tesseract. Mitigations: tight regions, preprocessing, retries, and debug captures.
- **Name matching:** a keyword can appear in another item's name, which would then be bought. Mitigation: dry runs log every name they read; make the keyword more specific (e.g. `"covenant bookmark"`) if it happens. A badly misread name skips a wanted item; setup checks that every row's name reads, and dry runs show what was read.
- **Row detection:** price text differs from row to row, so the buy-button match is never perfect. If the threshold is too strict, rows are missed; if it's too loose, the wrong things match. Mitigations: an exact count is required, rows must be evenly spaced, and the user sees the rows outlined with the name read for each before going on (§9.5). If detection fails, the user redraws the buy button box more tightly.
- **Starting currency misread:** every later decision depends on the two starting reads. Mitigations: keeping digits only, 3-read consensus, and the user confirming the values before starting. A dropped or extra digit is still possible if OCR is consistently wrong, and the confirmation step catches that.
- **Network latency after confirming (known limitation, to tackle later):** the shop belongs only to this user, so nothing else can buy an item or refresh the shop during a run. The risk is latency instead. After the app clicks the button in the buy or refresh dialog, the game may not finish the action straight away, because it waits for the server. v1 records the spend as soon as it clicks and goes on to the next step after the usual 100–200 ms delay. If the game is slow, problems can follow:
  - the next click lands while a loading or dialog screen is still showing
  - the next slot is read before the list updates
  - after a refresh, the old items are scanned again
  - the record of spending gets out of step with the game

  The current checks partly guard against this: each dialog's text is checked with retries, and a missing "buy" or "confirm" stops the run. They don't confirm that the action finished. **Future fix:** after each confirm click, wait for the result before continuing (e.g. the dialog has closed, the slot shows "sold out", or the list contents have changed after a refresh), with a timeout. Record the spend only once the result is seen, and stop the run if the timeout passes.
- **UI timing:** popups and the refresh animation take time. The OCR retry loop handles this instead of fixed sleeps.

## 9. First-run Setup (phase 3)

This replaces hand-editing the JSON. The user marks regions on screenshots of the game in a wizard. The wizard checks each one with OCR and then writes the config.

### 9.1 When it runs

- **GUI boot, no config:** the config path is the one remembered in `QSettings`, or `config.json` next to the app. If that file doesn't exist, the app writes the **built-in default layout** there (`defaults.py`, measured on the real shop at 1243×699) and loads it; there's no setup to do. Setup stays available for users whose game doesn't line up with the defaults.
- **Config exists but is invalid** (for example the old keyword format): a message shows the validation error, with **Run setup** and **Browse…** buttons.
- **Any time:** a **Run setup…** button in the Config box. It's also offered when a run refuses to start because the window's shape changed (§9.6).
- **Command line:** no config → it writes the default layout too and carries on. Setup is GUI-only.

### 9.2 Wizard steps

The user does everything in the game. The only exception is scrolling, which the app does itself (§9.5). Setup never clicks in the game.

| # | Page | The user, in the game | The user marks on the screenshot | Checked before Next |
|---|---|---|---|---|
| # | Page | The user, in the game | The user marks on the screenshot | Checked before Next |
|---|---|---|---|---|
| 1 | Capture the shop | starts Epic Seven, sets the window size, opens the Secret Shop with the list at the top, then presses **Capture** | — (a Tesseract path, only when none is bundled) | Tesseract found; `EpicSeven.exe` running; the client size is recorded |
| 2 | Shop screen | — | "Secret Shop" title, gold digits, skystones digits, Refresh button | title OCR contains `secret shop`; gold and skystones each read a number |
| 3 | Items, page 1 | — | name + buy button of **any one** item | 4 evenly spaced rows found; every row's name reads |
| 4 | Items, page 2 | — (the app scrolls and captures) | name + buy button of the **bottom** item | both new rows' names read |
| 5 | Buy dialog | clicks any item's buy button, then presses **Capture**; afterwards presses **Cancel** in the game | dialog text region, confirm button | text contains `buy` |
| 6 | Refresh dialog | clicks Refresh, then **Capture**; afterwards **Cancel** | dialog text region, confirm button | text contains `confirm` |
| 7 | Finish | — | — | summary (window size, every region, where it will be saved); **Save** |

Every page gives its instructions as a numbered list. The capture page's first step asks for the game window to be set to the size the user plays at; the Finish page repeats the rule in full (don't resize afterwards, moving is fine). The app's font is the system font plus 2 px. The dialog pages end with "**Don't confirm:** press Cancel in the game".

Number boxes (gold and skystones) must hold **only the digits**. On the real shop, a gold box that included the coin icon read its "+" as a leading 6 or 8, and the three startup reads disagreed.

Qt on Windows creates an Aero-style back arrow in the wizard's top-left corner, which the Windows 11 theme shows even in the classic style. The wizard hides it; the classic Back button is at the bottom.

### 9.3 Capturing

- `SetupSession` (`setup_session.py`) wraps `GameWindow.find`, `focus`, `client_size`, an `mss` grab of the whole client area, the scroll from `Mouse.scroll_in`, and `TesseractOcr`. It's a protocol, so the wizard is tested with a fake session.
- **The wizard gets out of the way during a capture.** `mss` captures screen pixels, so a wizard on top of the game would end up in the shot. The order is: make the wizard and the main window fully transparent, focus the game (which also puts it above them), wait `after_focus_ms`, grab, then make them opaque again and bring the wizard back to the front. The wizard isn't hidden, because hiding a modal dialog ends it.
- **The first capture (page 2) records the client size.** Every later capture compares against it. On a mismatch the wizard says: "The game window changed from 1920×1080 to 1600×900. Setup has to start again from the shop capture." Then it goes back to page 2 and clears what was marked.
- **OCR checks run on crops of the in-memory screenshot**, not new captures, so they check exactly what the user marked.
- Every screenshot is also saved to `debug/setup-<timestamp>/` for troubleshooting.

### 9.4 Marking regions (`gui/region_picker.py`)

- A `QGraphicsView` shows the screenshot fitted to the view. The mouse wheel zooms and a right/middle drag pans, so small regions like the currency numbers can be marked precisely.
- A side list shows the regions this page needs. Clicking one selects it, and a left-drag draws it; drawing again replaces it. Regions already marked on the page stay visible, with their labels.
- For the selected region, the page shows its crop enlarged and the live OCR result (e.g. `Gold: 12,345,678 ✓` or `Title: "secret sh0p" ✗`).
- View coordinates map back to image pixels, which are client-area coordinates. They're rounded and clamped to the image. Regions smaller than 4 px on a side are rejected.

### 9.5 Finding the other item rows

Every row has the same layout and the rows are evenly spaced, so one marked row per page is enough. Call its name region **N** and its buy button **B**.

**Page 1 (4 rows): detect them with a template match**
1. The template is B's crop from the page-1 screenshot.
2. Slide it **vertically only**, keeping B's x-range, over the screenshot's whole height. At each y, compute the normalized cross-correlation between the template and the window under it (numpy, grayscale). This is a 1-D search, so it's fast.
3. Peaks are local maxima above a threshold (start at 0.7, tune on samples) that are at least `B.height` apart. The price text differs from row to row, so the score is never 1.0; the button's shape and colour carry the match.
4. Accept the result only if there are **exactly 4 peaks**, one is at B's own y (±2 px), and the gaps are equal within ±3 px. `pitch` = the mean gap.
5. Row *i* = N and B shifted by `dy_i`.
6. Draw all 4 rows over the screenshot, with each name as read and the item it matches (`“covenant bookmarks” → Covenant Bookmarks`, `“army rescue dog”`). The page is complete as soon as the rows are found and every name reads; there's no separate confirmation.

If detection fails, the page says so and asks for a tighter buy-button box. (An earlier manual fallback, marking the top and bottom rows, was dropped to keep the page simple.)

**Page 2 (the 2 new rows): use page 1's pitch**
1. `scroll.area` = the bounding box of the 4 page-1 rows. Setup scrolls **one notch at a time**, waiting for the list to stop moving after each, until a notch changes nothing. `scroll.notches` = the notches that moved it, plus one spare. Extra notches do nothing once the list is at the bottom, so every run lands on the same position. (The first version always sent 10 notches, 30–80 ms apart; together with a fixed 600 ms wait, the scroll took ~1.9 s of a ~6.2 s round.)
   During a run, page 2 is read as soon as two looks at the list 80 ms apart match (`settle_check_ms`), instead of after a fixed wait; `after_scroll_ms` (1.5 s) is only the limit. The refresh dialog is checked every 100 ms (8 tries) instead of every 300 ms (3 tries): in the first live run it was missed on the first look in 7 rounds out of 10.
2. Once a notch stops moving the list, the app captures page 2. If no notch moved it, or the list area looks the same as on page 1, scrolling failed: the wizard says so, and **Scroll and capture** can be pressed again.
3. The user marks the **bottom** row. The other new row is the same rectangles shifted up by `pitch`.
4. As a cross-check, the template match from page 1 runs on this screenshot too. A warning (not a block) is shown if it doesn't find rows at both positions.
5. An overlay with the names read appears; the page is complete once both names read.

Only explicit rectangles go into the config, so the runner doesn't need to know about the pitch.

### 9.6 Window size

- `window_size` is the client size recorded on page 2, written on **Save**.
- It's the reference size the regions are scaled from (§3.1); any window of the same shape works. Only a different shape stops a run, with a message that has a **Run setup** button.

### 9.7 Saving

- The wizard builds the config from the measured coordinates plus every other setting. Those come from the config that was loaded, if there is one (`wanted_items`, `ocr`, `timings`, `abort_hotkey`, folders), otherwise from the `config.example.json` defaults. The Tesseract path is written only when there's no bundled copy and the user entered one on the capture page.
- The result is validated through `Config` before anything is written. A validation error at this point is a bug, so it's shown in full.
- If a file is being replaced, the old one is kept as `config.json.bak`.
- The path is remembered in `QSettings`, and the main window loads it straight away.

### 9.8 Code

| Module | Contents |
|---|---|
| `calibrate.py` | `find_rows(image, marked, expected)`, `rows_above(bottom, pitch, count)`, `scores_at(...)`, `scroll_area(rows)`, `region_changed(...)`, `build_config(measured, base) -> dict`, `save_config`. Pure functions: no Qt, no Win32. |
| `setup_session.py` | the `SetupSession` protocol and `WindowsSetupSession` |
| `gui/region_picker.py` | the screenshot view and region list |
| `gui/setup_wizard.py` | `SetupWizard(QWizard)` with the 8 pages, built around an injected `SetupSession` |
| `gui/main_window.py` | the boot check, **Run setup…**, and the size-mismatch button |
| `shop.py` | the name read with its recovery and skip; `click_through_dialog` |
| `errors.py` | `WindowSizeMismatch(StopRun)`, so the GUI can tell a size mismatch apart and offer setup |
| `pyproject.toml` | add `numpy` |

### 9.9 Tests

- `test_calibrate.py`: synthetic screenshots with 4 identical buttons that have different text inside, which should give 4 rows and the right pitch. 3 or 5 matches and uneven spacing should be rejected. Also: page-2 rows from the pitch, the scroll area, `near_miss`, and `build_config` producing a valid `Config` that keeps the base config's prices and timings. The same checks run on real screenshots in `tests/samples/` when they're present, and are skipped otherwise.
- `test_region_picker.py`: view↔image mapping at several zoom levels, clamping, and the minimum size.
- `test_setup_wizard.py`: a fake session returns synthetic screenshots and canned OCR. Walk every page and check the saved file loads. Also: the size changes mid-setup → back to page 2; a failed OCR check blocks Next; Cancel writes nothing; detection fails → a tighter box fixes it.
- `test_gui.py`: no config at boot → the wizard opens (through an injectable hook); an invalid config → the message with Run setup.
- `test_config.py`, `test_runner.py`, `fakes.py`: name-based slots, and every recovery path: dropped clicks, unread dialogs, dialogs left open, names that read late or never.
