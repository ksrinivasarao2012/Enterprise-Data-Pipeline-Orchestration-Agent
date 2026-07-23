# PDF fonts

Used by `src/services/report_service.py` so the incident PDF can print accents, Cyrillic, Greek,
Hindi (Devanagari) and Telugu instead of `?`.

- `NotoSans-*.ttf`: Latin (with accents), Cyrillic, Greek and Devanagari (Hindi, Marathi, Nepali)
- `NotoSansTelugu-*.ttf`: Telugu

Both are Noto fonts by The Noto Project Authors, licensed under the SIL Open Font License 1.1
(see `OFL.txt`). Regular and Bold only, to keep the repository small (about 1.6 MB).

Telugu and Hindi also need text shaping (letters join into conjuncts, vowel signs move). The PDF
library does that with `uharfbuzz`, which is in `requirements.txt`.

To support another script (Tamil, Kannada, Bengali...), add its Noto font here and list it in
`FONT_FILES` in `report_service.py`. Colour emoji and Chinese/Japanese/Korean are not included:
emoji print as `[emoji]`, CJK as `?`.
