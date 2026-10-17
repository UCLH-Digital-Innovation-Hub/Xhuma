# Dates in the converted record

Dates retain the precision supplied by GP Connect. A year stays a year, and a month stays a month. Structured timestamps also retain the time, fractional seconds and timezone when provided. The converter does not invent midnight for a date without a time.

The shared helpers in `app/ccda/helpers.py` distinguish three CDA representations:

| Helper | Representation | Use |
| --- | --- | --- |
| `cda_timestamp` | TS | A standalone timestamp, such as authorship time |
| `cda_time_bound` | IVXB_TS | The start or end of an interval |
| `cda_time_interval` | IVL_TS | An interval with both boundaries explicitly present |

They share one formatting function, `fhir_to_cda_timestamp`, which reads the original FHIR value using `as_json()`. The FHIR library's `isostring` property can normalise partial dates and lose fractional seconds.

An absent date passed to a typed helper becomes explicitly unknown (`nullFlavor="UNK"`). Clinical mappings that omit an absent endpoint construct their interval directly. Medication duration retains this omission behaviour. Duration and dosing frequency remain separate effective-time elements; medication serialization no longer merges artificial low/high operator objects.

These helpers do not choose the clinical date. Allergy onset, authorship, specimen collection and report finalisation retain their separate meanings. The investigation mapping documents the Epic-specific distinction between collection and finalisation.

## Dates shown to readers

`readable_date` formats full CDA dates as `DD/MM/YYYY`, including when the structured timestamp contains a time and offset. It does not shift dates between timezones. A partial date displays as `YYYY-MM` or `YYYY`. The input is validated before formatting; empty or malformed timestamps raise an error. Callers explicitly handle absent dates as blank cells or their existing missing-date wording.

## Active and past medications

An active medication remains active. A completed medication with an end date after today is also displayed as active, with the existing explanatory warning. Other medications remain in the past section.

The comparison uses the supplied calendar date, ignoring time of day and without shifting timezone. An end date of today does not count as future. For partial dates, a future year or month counts as future; the current year or month alone cannot establish a future end date. No day or month is invented to make this comparison. Missing end dates do not count as future.
