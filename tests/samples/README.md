# OCR samples

Real screenshots from the game, used to tune OCR (phase 1, steps 3–4).

1. Run `python -m e7shop inspect --config config.json` on each screen you want to cover. It saves the whole window and every configured region to `debug/inspect-<timestamp>/`.
2. Copy the useful images here.
3. List them in `cases.json`:

```json
[
  { "image": "shop_top.png", "kind": "text", "expect": "secret shop" },
  { "image": "shop_top.png", "kind": "text", "expect": "covenant", "region": [1100, 200, 1700, 290] },
  { "image": "buy_dialog.png", "kind": "text", "expect": "buy", "region": [600, 300, 1300, 750] },
  { "image": "refresh_dialog.png", "kind": "text", "expect": "confirm", "region": [600, 300, 1300, 750] },
  { "image": "currency_gold.png", "kind": "number", "expect": 12345678 }
]
```

`region` is optional and crops the image first. Text cases pass when `expect` appears in the lower-cased OCR text; number cases compare the parsed integer.

The tests are skipped when Tesseract isn't on `PATH` or `cases.json` doesn't exist.
