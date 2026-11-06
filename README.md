# e7shop

Epic Seven Secret Shop helper. It buys every Covenant Bookmark and Mystic Medal in the shop, refreshes, and repeats, until it has done the number of refreshes you asked for or runs out of skystones or gold. Items are recognised by their name: a keyword ("covenant", "mystic") in the OCR'd item name. See [PLAN.md](PLAN.md) for the full design.

Run it without a command to open the GUI; `run` and `inspect` are also available from the command line.

## Packaged app

`E7SecretShop.exe` is a single file with everything included, Tesseract OCR too: nothing else needs installing.

1. Put `E7SecretShop.exe` in a folder of its own and double-click it. Windows asks for administrator rights every time (UAC); the app can't start without them.
2. The first time, it creates `config.json` with the built-in default layout, so there's nothing to set up. If the regions don't line up with your game, use **Run setup…** (see [Setup](#setup)). Any window size works: the regions are scaled to it.

`config.json` and the `runs/`, `logs/` and `debug/` folders are created next to the exe. The exe is the GUI only; the `run` and `inspect` commands need the source setup below. It takes a few seconds to start, because it unpacks itself to a temporary folder first.

### Building it

On Windows, from the project folder:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

From WSL, which copies the working tree to `%USERPROFILE%\e7shop-build` and builds there (Windows Python can't build reliably from a `\\wsl$` path):

```bash
scripts/build-from-wsl.sh
```

For a release, add `-Release` (Windows) or `--release` (WSL):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1 -Release
```

A release build doesn't write the `debug` folder (screenshots of failed reads and of setup). Logs and run records are still written. Set `"save_debug": true` in the config to turn debug screenshots back on in a release build, or `false` to turn them off elsewhere.

Either way, the script:

1. creates `.venv-build` with PyInstaller;
2. prepares `vendor/tesseract/` with `scripts/fetch_tesseract.py`: it downloads the pinned [UB Mannheim](https://github.com/UB-Mannheim/tesseract) installer (checksum-verified), unpacks it with 7-Zip without running it, and keeps only `tesseract.exe`, the DLLs it loads, English language data and the licences. From WSL the DLLs are also stripped of debug info, which cuts them from 121 MB to 23 MB; Windows usually has no `strip`, so a build there is bigger;
3. builds a console variant of the same bundle without the admin manifest and runs its `--self-test`: it imports everything, builds the windows, grabs the screen, and reads a rendered price and text with the bundled Tesseract;
4. only if that passes, builds `dist\E7SecretShop.exe` from `build.spec`.

Building needs [7-Zip](https://www.7-zip.org/) installed on Windows.

The bundled Tesseract is Apache-2.0 (its licence is in `vendor/tesseract/licenses/`). Its DLLs come from the UB Mannheim build and carry their own licences, including LGPL (libiconv) and GPL (libjbig, used by libtiff). Check those before distributing the exe publicly.

## Setup from source (Windows)

It must run with Windows Python, not inside WSL.

1. Get Tesseract OCR: either run `python scripts\fetch_tesseract.py` (puts it in `vendor\tesseract`, which the app finds by itself), or install [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki) to its default path `C:\Program Files\Tesseract-OCR\tesseract.exe`.
2. From the project folder, in PowerShell:

   ```powershell
   py -3.12 -m venv .venv
   .venv\Scripts\pip install -e ".[dev]"
   ```

3. Start the GUI (below). With no config yet, it creates `config.json` with the default layout.

The app always runs as administrator. When it isn't elevated, it relaunches itself through the UAC prompt: the GUI without a console window, and the command line in a new console that waits for Enter before closing.

## Usage

### Setup

The app ships with a default layout, measured on the real shop, so setup is optional. Run it with **Run setup…** if the defaults don't line up with your game (for example, a different game language or display setup). Setup measures everything again at your window's size.

1. Start Epic Seven and **set the game window to the size you'll play at**. Everything you mark is tied to that size: moving the window is fine, but after resizing it you need to run setup again.
2. Open the Secret Shop with the item list at the top, and press **Capture**. The setup starts right there.
3. Mark the "Secret Shop" title, your gold, your skystones and the Refresh button. Each one is checked with OCR as you mark it. Box **only the digits** of gold and skystones: a coin icon's "+" inside the box gets read as an extra digit.
4. Mark the name and the buy button of any one item. Setup finds the other 3 rows and shows the name it reads for each, and which wanted item it matches. If it can't find them, redraw the buy button box more tightly.
5. Press **Scroll and capture**: setup scrolls the list to the bottom, the same way a run does. Mark the bottom item; the other new item is worked out from the row spacing.
6. Open an item's buy dialog in the game, press **Capture**, and mark the dialog text and confirm button. Then press **Cancel** in the game. Do the same for the refresh dialog.
7. Press **Save**. An existing config is kept as `config.json.bak`, and your wanted items and other settings carry over.

Setup never clicks in the game. Its screenshots are saved to `debug/setup-<timestamp>/`.

### GUI

```powershell
.venv\Scripts\python -m e7shop
```

1. Pick your config file, or make one with **Run setup…**. The app remembers it next time.
2. Open the Secret Shop in Epic Seven and click **Read from game**. The gold and skystones it read appear in the boxes.
3. Check the values, correct them if needed, and tick **These values are correct**. Changing a value unticks it.
4. Choose **Refresh N times** or **Until out of skystones or gold**, then click **Start**, or **Dry run** to try it without clicking.
5. Watch the progress panel and log. **Stop** or **F12** ends the run.

After a run, the gold and skystones left are carried over into the boxes for the next run. They still need confirming.

### Command line

Open the Secret Shop in Epic Seven, then:

```powershell
.venv\Scripts\python -m e7shop inspect                   # check the config: capture + OCR every region
.venv\Scripts\python -m e7shop run --count 1 --dry-run   # read and move the mouse, never click
.venv\Scripts\python -m e7shop run --count 10            # refresh up to 10 times
.venv\Scripts\python -m e7shop run --until-empty         # refresh until out of skystones or gold
```

Before starting, `run` shows the gold and skystones it read and asks you to confirm or correct them. Press **F12** (`abort_hotkey`) at any time to stop.

Output (relative folders are next to the config file):
- `runs/<timestamp>.jsonl`: every purchase and refresh, plus start and stop records
- `logs/<timestamp>.log`: detailed log, including every OCR read
- `debug/`: screenshots saved when an OCR check fails or during setup, and `inspect` output. Not written by release builds unless `save_debug` is `true`.

## Config

Setup writes the config; editing it by hand is optional. All rectangles are `[x0, y0, x1, y1]` (top-left and bottom-right corners) in pixels, **relative to the game window's client area**. `window_size` is the size the regions were measured at; at run time they're scaled to the game window's current size, so resizing the game is fine. The game keeps its window 16:9; if the shape is ever different, the app offers to run setup again. `inspect` prints the current size.

| Key | What it is |
|---|---|
| `tesseract_path` | optional: a Tesseract to use instead of the bundled one (or the standard install) |
| `currency.gold_region`, `currency.skystones_region` | where the balances appear in the top bar |
| `pages[0].slots` | item slots visible with the list at the top: each has a `name_region` (OCR'd to identify the item) and a `buy_button` |
| `pages[1].slots` | after scrolling to the bottom, only the slots page 1 didn't cover |
| `scroll` | where to scroll, how many wheel notches (negative = down; setup counts the notches to the bottom and adds one spare) and the gap between notches |
| `buy_dialog`, `refresh_dialog` | text region checked for "buy" / "confirm", and the button to click |
| `refresh_button` | the shop's Refresh button |
| `wanted_items` | display name, `keyword` (matched case-insensitively in each slot's name text; must be unique) and gold price (for the ledger and the gold checks; it isn't read from the shop) |
| `save_debug` | optional: write debug screenshots to `debug_dir` (`true`/`false`). Default: on, except in release builds |
| `timings` | wait after focusing and after refreshing; after scrolling, the list is checked every `settle_check_ms` and read as soon as it stops moving, for at most `after_scroll_ms` |

To check a config: run `inspect` and look at each region's crop and OCR text in `debug/inspect-*/`.

**Recovering instead of stopping:**
- **An item name doesn't read:** if the buy or refresh dialog is still open (its confirm click didn't land), the app confirms it to finish that step, then reads the name again. Otherwise it waits for the list to stop moving and reads again. If it still can't, it skips the item, with a warning, a debug capture and a "Skipped items" line in the summary.
- **A wanted item is already bought:** its buy button is greyed out ("0/1 Buy"), which happens when a refresh didn't go through and the shop is the one just bought from. The app skips it without clicking, with a warning and an "Already bought" line in the summary.
- **The buy or refresh dialog isn't recognised after a click:** if the button got clearly darker than before the click, the dialog must be open (an open dialog dims the shop; its text just didn't read), so its confirm button is clicked. Otherwise the click didn't register, so it's clicked again (up to 3 clicks, then the run stops). The "before" look waits for the screen to settle, so a dialog still fading out can't fool it.

Configs from older versions (slots with `target` or `price_region`) don't load: run setup to replace them. Setup keeps your wanted items, and gives items without a keyword the first word of their name.

## Tests

```powershell
.venv\Scripts\python -m pytest
```

The run loop is tested against a simulated shop (`tests/fakes.py`), so the game isn't needed. For OCR tests on real screenshots, see `tests/samples/README.md`.

## Known limitations

- The app doesn't wait for the server after clicking a buy or refresh confirmation (PLAN.md §8).
- Regions are scaled to the game window's size. Scaling was tested from 0.6x to 1.4x of the default layout's 1243x699 on one installation; the buy and refresh dialogs weren't part of that test. Fullscreen and very large windows weren't tested.
- Items are recognised by a keyword in their name, so another item whose name contains "covenant" or "mystic" would be bought too. Dry runs log every name they read.
