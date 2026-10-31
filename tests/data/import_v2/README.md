# Import v2 test data

Small files for `tests/test_datasets.py`. Everything here is synthetic except one excerpt.

- `eclab-cell07.mpt`: an EC-Lab-style GCPL export (6 header lines, tab-separated with a trailing tab, CRLF line
  endings, Windows-1252 text with a degree sign in `Temperature/°C`). Synthetic values.
- `cell08-mv.csv`: a plain CSV with a unit row, time in minutes and voltage in millivolts. Synthetic.
- `cell09-thermal.json`: JSON columns, temperature in kelvin. Synthetic.
- `teardown-cell07.png`: a 24 x 16 synthetic image.
- `manifest.json`: a v2 manifest for the four files above.
- `v1/`: a v1 experiment manifest with its voltage and timestamp CSVs; the test writes the video.
- `.gitattributes`: keeps these bytes exactly as written (no line-ending conversion).

## LGM50_cell02_excerpt.csv (real data, CC BY 4.0)

The Maccor preamble and header, then records 2601 to 2900 (300 rows), unchanged, of `LGM50_cell02.csv` from:

Chen, C.-H., Brosa Planella, F., O'Regan, K., Gastol, D., Widanage, W. D., Kendrick, E. Experimental data for
"Development of Experimental Techniques for Parameterization of Multi-scale Lithium-ion Battery Models". Zenodo,
2020. doi:[10.5281/zenodo.4032561](https://doi.org/10.5281/zenodo.4032561). Paper: J. Electrochem. Soc. 167,
080534 (2020), doi:10.1149/1945-7111/ab9050.

Licence: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/). The only
change is the excerpt (rows removed before and after); no value was altered. The full file is 597,715 bytes with
sha256 `650e7f9ac5217b4db158047e50b6b464100b13f4859002421114edebca629b35`. The excerpt keeps the rest-to-charge
step change and the pair of rows (records 2685 and 2686) logged at the same test time. `chen2020-manifest.json`
describes it.
