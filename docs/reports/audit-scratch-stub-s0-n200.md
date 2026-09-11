# Audit scratch-stub-s0-n200

> **Not a measurement.** This report comes from a code-path check, not from the pilot run. The
population is 20 calibration pages of three Honkoku-Lines items whose character boxes were placed by
a stub detector (evenly spaced boxes inside each line box) and scored by a stub classifier (one
probability for every code point), and the 24 reviewed units were decided by a script rather than by
a reviewer. The numbers below show that sampling, the blind queue, the review events, `apply` and the
weighted report fit together on real page and line records; they say nothing about the pipeline's
precision. The pilot report replaces this file once the detector exists and reviewers have worked the
sample.

Sample `scratch-stub-s0-n200` of the run `scratch-stub`: 205 units drawn from 2843 accepted units on 20 pages, seed 0, strata document, script.

The draw is simple random sampling without replacement within each stratum, applied to the units the pipeline accepted and no earlier audit sample holds. A unit's inclusion probability is n_stratum / n_population_of_stratum.

A sampled unit is scored against the unit the tables hold now: box right at IoU 0.5 or more with the prediction, label right when the code point agrees, joint when both do; a unit the review retired counts as wrong. Rates are weighted by the inverse of each unit's inclusion probability.

Reviewed 24 of 205 sampled units; 181 untouched, 0 retired.

## The audited units

| measure | value | Wilson 95% | cluster bootstrap 95% | n |
| --- | ---: | --- | --- | ---: |
| box precision | 0.6927 | 0.4671 to 0.8203 | 0.6387 to 0.7423 | 16 |
| label precision | 0.3442 | 0.1797 to 0.5329 | 0.2742 to 0.4085 | 8 |
| joint precision | 0.3442 | 0.1797 to 0.5329 | 0.2742 to 0.4085 | 8 |

This is the published figure: it covers the units a reviewer has decided on. The table below it covers the whole sample, where an untouched unit stands in for itself and therefore reads as right; it is a progress figure, not a measurement.

## The whole sample

| measure | value | Wilson 95% | cluster bootstrap 95% | n |
| --- | ---: | --- | --- | ---: |
| box precision | 0.9643 | 0.9249 to 0.9801 | 0.9066 to 1.0000 | 197 |
| label precision | 0.9238 | 0.8770 to 0.9514 | 0.8004 to 1.0000 | 189 |
| joint precision | 0.9238 | 0.8770 to 0.9514 | 0.8004 to 1.0000 | 189 |

## By stratum

| stratum | population | sampled | box precision | 95% | label precision | 95% | joint precision | 95% |
| --- | ---: | ---: | ---: | --- | ---: | --- | ---: | --- |
| hl:0c016d24723ee3568663547b0f9ef40b|hiragana | 1 | 1 | 0.0000 | 0.0000 to 0.7935 | 0.0000 | 0.0000 to 0.7935 | 0.0000 | 0.0000 to 0.7935 |
| hl:0c016d24723ee3568663547b0f9ef40b|kanji | 260 | 12 | 0.7500 | 0.4677 to 0.9111 | 0.3333 | 0.1381 to 0.6094 | 0.3333 | 0.1381 to 0.6094 |
| hl:0c016d24723ee3568663547b0f9ef40b|katakana | 257 | 10 | 0.6000 | 0.3127 to 0.8318 | 0.3000 | 0.1078 to 0.6032 | 0.3000 | 0.1078 to 0.6032 |
| hl:0c016d24723ee3568663547b0f9ef40b|unknown | 26 | 1 | 1.0000 | 0.2065 to 1.0000 | 1.0000 | 0.2065 to 1.0000 | 1.0000 | 0.2065 to 1.0000 |

An audit sample is not a training set: no threshold, cost or model may be tuned on it, and `atlas audit sample` leaves out every unit an earlier sample holds.
