# e7shop

Epic Seven Secret Shop helper. It buys every Covenant Bookmark and Mystic Medal in the shop, refreshes, and repeats, until it has done the number of refreshes you asked for or runs out of skystones or gold. See [PLAN.md](PLAN.md) for the full design.

Phase 1 is a command-line tool. The GUI comes in phase 2.

## Setup (Windows)

It must run with Windows Python, not inside WSL.

1. Install [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) (default path `C:\Program Files\Tesseract-OCR\tesseract.exe`).
2. From the project folder, in PowerShell:

   ```powershell
   py -3.12 -m venv .venv
   .venv\Scripts\pip install -e ".[dev]"
   copy config.example.json config.json
   ```

3. Edit `config.json` with your coordinates (see below).

The app always runs as administrator. When it isn't elevated, it relaunches itself through the UAC prompt in a new console window.

## Usage

Open the Secret Shop in Epic Seven, then:

```powershell
.venv\Scripts\python -m e7shop inspect                   # check the config: capture + OCR every region
.venv\Scripts\python -m e7shop run --count 1 --dry-run   # read and move the mouse, never click
.venv\Scripts\python -m e7shop run --count 10            # refresh up to 10 times
.venv\Scripts\python -m e7shop run --until-empty         # refresh until out of skystones or gold
```

Before starting, `run` shows the gold and skystones it read and asks you to confirm or correct them. Press **F12** (`abort_hotkey`) at any time to stop.

Output:
- `runs/<timestamp>.jsonl`: every purchase and refresh, plus start and stop records
- `logs/<timestamp>.log`: detailed log, including every OCR read
- `debug/`: screenshots saved when an OCR check fails, and `inspect` output

## Config

All rectangles are `[x0, y0, x1, y1]` (top-left and bottom-right corners) in pixels, **relative to the game window's client area**. `window_size` must match the game window's client size. `inspect` prints the current size.

| Key | What it is |
|---|---|
| `currency.gold_region`, `currency.skystones_region` | where the balances appear in the top bar |
| `pages[0].slots` | item slots visible with the list at the top |
| `pages[1].slots` | after scrolling, only the slots page 1 didn't cover |
| `scroll` | where to scroll, and how many wheel notches (negative = down) |
| `buy_dialog`, `refresh_dialog` | text region checked for "buy" / "confirm", and the button to click |
| `refresh_button` | the shop's Refresh button |
| `wanted_items` | keyword matched in each slot's text (case-insensitive), and gold price |
| `timings` | waits after focusing, scrolling and refreshing |

To find coordinates: run `inspect`, open `debug/inspect-*/window.png` in an image editor, and read the pixel positions. Then run `inspect` again and check that each region's crop and OCR text look right.

## Tests

```powershell
.venv\Scripts\python -m pytest
```

The run loop is tested against a simulated shop (`tests/fakes.py`), so the game isn't needed. For OCR tests on real screenshots, see `tests/samples/README.md`.

## Known limitations

- The app doesn't wait for the server after clicking a buy or refresh confirmation (PLAN.md §8).
- The window size must match `window_size` exactly; there's no scaling yet.
