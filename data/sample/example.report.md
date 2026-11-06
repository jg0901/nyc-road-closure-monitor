# Run 20261108T120000Z — SUCCESS

- Dataset: Street Closures (fixture)
- Source URL: https://data.cityofnewyork.us/resource/i6b5-j7bu.json
- Source last updated (UTC): 2025-11-05T00:00:00Z
- Fetched at (UTC): 2026-11-05T15:21:23Z
- Rows fetched: 5  |  normalized: 5
- Log: logs/20261108T120000Z.log

## Changes vs 20261106T120000Z
| added | modified | unchanged | missing |
|---|---|---|---|
| 1 | 2 | 2 | 1 |

Fields changed:
- `work_end_date`: 1
- `purpose`: 1

### added (showing 1 of 1)
- `1006` {"segmentid": "S-600", "onstreetname": "CANAL ST", "fromstreetname": "BOWERY", "tostreetname": "CENTRE ST", "borough": "MANHATTAN", "work_start_date": "2026-11-10", "work_end_date": "2026-11-17", "purpose": "CRANE"}

### modified (showing 2 of 2)
- `1001` {"work_end_date": {"from": "2026-11-20", "to": "2026-12-05"}}
- `1003` {"purpose": {"from": "MILLING", "to": "RESURFACING"}}

### missing (showing 1 of 1)
- `1005` {"segmentid": "S-500", "onstreetname": "VICTORY BLVD", "fromstreetname": "BAY ST", "tostreetname": "JERSEY ST", "borough": "STATEN ISLAND", "work_start_date": "2026-11-02", "work_end_date": "2026-11-09", "purpose": "WATER MAIN"}

## Quality
- key check ok: True  |  duplicate keys: 0  |  null-key rows: 0
- missing required: {}
- unparseable dates: {}
- absent configured fields: []

_Note: 'missing' means not present in this snapshot, not that the road reopened._
