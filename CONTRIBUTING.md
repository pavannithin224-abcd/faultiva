# Contributing

Faultiva is a research artifact from an undergraduate major project. Issues and pull
requests are welcome, with one constraint that shapes everything below.

## The frozen-evidence rule

Measured results in this repository are **frozen**. Model weights, the decision threshold
(0.4965), the signature dictionary, and every reported metric are anchored to hash-verified
audit artifacts published in the
[V2 study repository](https://github.com/pavannithin224-abcd/circuitsage-vlsi-fault-detection-v2).

A pull request must not retrain the model, change the threshold, regenerate the dictionary,
or edit a reported number. If you believe a measurement is wrong, open an issue with the
reproduction steps and it will be investigated as a defect.

Code, documentation, packaging, and new circuit characterizations are all fair game.

## Useful contributions

**Characterize a new circuit.** This is the highest-value contribution. Localization needs a
signature dictionary per circuit, and only four ship today. The pipeline is documented in
the V2 repository; a new dictionary plus its provenance is a genuine extension.

**Port the feature pipeline.** `faultiva/feature_pipeline.py` is pure numpy and could be
reimplemented elsewhere. Any port must reproduce the reference vectors exactly.

**Improve packaging and docs.** Type hints, error messages, examples, and API ergonomics.

## Before opening a pull request

```bash
pip install -r requirements.txt
sha256sum -c SHA256SUMS
python examples/predict_one.py
python examples/end_to_end.py
```

CI runs these on Python 3.10, 3.11 and 3.12, plus two regression assertions: the model must
not be saturated, and out-of-scope circuits must return `OUT_OF_SCOPE`.

## Reporting a defect

Include the circuit, the inputs, what you expected, and what you observed. If a verdict looks
wrong, please say which stage produced it — detection, localization, and verification fail in
different ways and the distinction speeds up diagnosis considerably.
