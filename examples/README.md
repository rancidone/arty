# Offline examples

Run these from the cloned repository with Arty installed and its virtual
environment activated (see the [quickstart](../README.md)). All inputs are
committed synthetic fixtures. No API key, network access or generation is needed.
Each output path must be new; choose a fresh revision folder to repeat a command.

```sh
arty convert examples/slime-enlarged.png -o work/slime.png --size 96x64 --columns 3 --rows 2 --preview work/preview.png
arty build examples/specs/build.json -o work/build-r01
arty verify work/build-r01
arty inspect work/build-r01
arty review work/build-r01 -o work/build-review.png
arty rebuild work/build-r01 --revision native-r02 -o work/build-r02
arty atlas examples/specs/atlas.json -o work/atlas-r01
arty extract work/atlas-r01 -o work/cells-r01
arty fit examples/specs/fit.json -o work/fit-r01
```

Expected results:

- `work/slime.png` is pixel-identical to `slime-original.png`; metadata describes
  six 32×32 frames. `work/preview.png` shows the source grid and sampled sheet.
- `work/build-r01/preview.png` matches `slime-original.png`; `preview-2x.png` is
  exactly nearest-neighbor doubled. Verification passes; artistic approval is pending.
- `work/build-review.png` includes the reference, candidate, reconstructed layers
  and registered difference. Its `.review.json` binds the board to revision hashes.
- `work/build-r02` rebuilds from snapshots without needing original source paths.
- `work/atlas-r01/preview.png` is 192×64: the first 96×64 cell contains the fixture,
  the second is transparent. Extraction writes both cells exactly.
- `work/fit-r01/comparison.png` compares two explicit candidates. Both technical
  checks pass; the reduced candidate has deliberately changed clusters and needs
  visual review. Its operations replace the target layer's operations.

The atlas treats the entire six-sprite fixture as one cell to demonstrate packing;
it does not infer poses or animation timing. See the [spec reference](../docs/specs.md)
for adapting these files to your own art. A source's pixel values, including hidden
RGB under transparent pixels, are preserved unless an explicit operation changes them.
