# Run 20261113T120000Z — SUCCESS

- Dataset: NYC DOT Weekly Traffic Advisory
- Source URL: https://www.nyc.gov/html/dot/html/motorist/weektraf.shtml
- Source last updated (UTC): n/a
- Fetched at (UTC): 2026-11-06T14:07:21Z
- Rows fetched: 7  |  normalized: 7
- Log: logs/advisory/20261113T120000Z.log

## Changes vs 20261106T120000Z
| added | modified | unchanged | missing |
|---|---|---|---|
| 1 | 1 | 5 | 2 |

Fields changed:
- `description`: 1

### added (showing 1 of 1)
- `cf5deb7dc1340fb3` {"kind": "closure", "section": "Queens", "section_id": "queens", "category": null, "name": "Northern Boulevard between 48th Street and 51st Street", "embargo": null, "updated": false, "streets": {}, "description": "One lane will be closed weeknights from 10pm to 5am through November 27, 2026."}

### modified (showing 1 of 1)
- `de70b35199115cbb` {"description": {"from": "As of Sunday October 25th, 2026, through Friday November 20th, 2026, a full northbound and southbound tube will continue to be closed (not at the same time), will take place every week 10pm Sunday night to 5am Friday morning.", "to": "As of Sunday October 25th, 2026, through Friday November 20th, 2026, a full northbound and southbound tube will continue to be closed (not at the same time), will take place every week 11pm Sunday night to 5am Friday morning."}}

### missing (showing 2 of 2)
- `051a2cc5f0efe9df` {"kind": "event", "section": "Manhattan", "section_id": "manhattan", "category": "Festivals, Parades and Events", "name": "The 53rd Annual New Yorks Village Halloween Parade", "embargo": "Embargo : Special Event Construction Embargo / October 28th, 2026, to October 31st, 2026", "updated": false, "streets": {"Formation": ["6th Avenue between Spring Street and Canal Street", "Dominick Street between Varick Street and 6th Avenue"], "Route": ["6th Avenue between Spring Street and 16th Street"], "Dispersal": ["West 16th Street between 6th Avenue and 7th Avenue"]}, "description": "The following streets will be closed Saturday October 31st, 2026, for the 53rd Annual New Yorks Village Halloween Parade."}
- `1145362e41d29f02` {"kind": "closure", "section": "Bronx", "section_id": "bronx", "category": null, "name": "Bruckner Expressway at Hunts Point Avenue", "embargo": null, "updated": false, "streets": {}, "description": "The exit will be closed intermittently Monday nights to Friday mornings from 10pm to 5am until May 12, 2027, to facilitate NYCDOT-Bridges removal and installation of steel beams."}

## Quality
- key check ok: True  |  duplicate keys: 0  |  null-key rows: 0
- missing required: {}
- unparseable dates: {}
- absent configured fields: []

_Note: 'missing' means not present in this snapshot, not that the road reopened._
