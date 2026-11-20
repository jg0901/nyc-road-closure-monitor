# Run 20261120T202715Z — SUCCESS

- Dataset: NYC DOT Weekly Traffic Advisory
- Source URL: https://www.nyc.gov/html/dot/html/motorist/weektraf.shtml
- Source last updated (UTC): n/a
- Fetched at (UTC): 2026-11-20T20:27:15Z
- Rows fetched: 19  |  normalized: 19
- Log: logs/advisory/weekly/20261120T202715Z.log

## Changes vs 20261113T183102Z
| added | modified | unchanged | missing |
|---|---|---|---|
| 3 | 6 | 10 | 3 |

Fields changed:
- `updated`: 6
- `description`: 1

### added (showing 3 of 3)
- `c21ce8eaef5c73f5` {"page": "weekly", "kind": "closure", "section": "East River Bridge Crossings", "section_id": "east", "category": null, "name": "Brooklyn Bridge", "embargo": null, "updated": true, "streets": {}, "description": "Manhattan-bound single lane, rolling closure (one lane at a time) will be closed Saturday November 1, 2026, from midnight to 5am for maintenance on the bridge as permitted by NYCDOT."}
- `232f29e3f46d6331` {"page": "weekly", "kind": "event", "section": "Manhattan", "section_id": "manhattan", "category": "Festivals, Parades and Events", "name": "100th Annual Macys Thanksgiving Day Parade", "embargo": "Embargo: Special Event Construction Embargo / November 17th, 2026, to November 26th, 2026", "updated": false, "streets": {"Streets": ["Review NYC DOT's construction embargos for special events"]}, "description": "The following streets will be closed Thursday November 26th, 2026, for 100th Annual Macys Thanksgiving Day Parade event at the Discretion of the NYPD in Manhattan"}
- `d8bc1bba851df272` {"page": "weekly", "kind": "event", "section": "Bronx/Manhattan", "section_id": "bxman", "category": "Festivals, Parades and Events", "name": "NYC Public Schools – Heroes of Highbridge 2 Mile Walk/Run", "embargo": null, "updated": false, "streets": {"Formation": ["Highbridge"], "Route": ["University Avenue between 170th Street and 168th Street", "168th Street between University Avenue and 167th Street", "167th Street between 168th Street and Sedgwick Avenue", "Sedgwick Avenue between 167th Street and Depot Place", "Depot Place between Sedgwick Avenue and Exterior Street", "Bridge Park"], "Dismissal": ["University Avenue between 170th Street and 168th Street"]}, "description": "The following streets will be closed Saturday November 21st, 2026, for NYC Public Schools-Heroes of Highbridge 2 Mile Walk/Run event at the Discretion of the NYPD in Bronx/Manhattan"}

### modified (showing 6 of 6)
- `a955db609ba36922` {"updated": {"from": true, "to": false}}
- `7b3ce820af9c068c` {"updated": {"from": true, "to": false}}
- `6ef13efd992fd989` {"updated": {"from": true, "to": false}}
- `28d25314408d42c0` {"updated": {"from": true, "to": false}}
- `9a822eff8626c105` {"updated": {"from": true, "to": false}}
- `0877fc39d1a97f08` {"description": {"from": "As of Wednesday November 11th, 2026, through Tuesday December 22nd, 2026, there will be a temporary detour to traffic on the Roosevelt Avenue Bridge over the Van Wyck Expressway between the hours of 11:59pm to 5am. During this temporary detour, the eastbound traffic lanes on Roosevelt Avenue Bridge will be closed and one lane of traffic will be maintained in the westbound direction only. Eastbound traffic will be detoured to the Northern Boulevard Bridge. As of Monday October 9th, 2026, through Friday November 20th, 2026, two southbound and one northbound lane on the Van Wyck Expressway will be closed 10pm to 5am single lane northbound and from 11pm to 5am double lane Southbound. One southbound and one northbound lane will remain open during these closures. These closures are necessary for the continued steel repair as part of the NYCDOT Roosevelt Avenue Bridge rehabilitation project.", "to": "As of Monday November 23rd, 2026, through Monday December 21st, 2026, two southbound and one northbound lane on the Van Wyck Expressway will be closed 10pm to 5am single lane northbound and from 11pm to 5am double lane Southbound. One southbound and one northbound lane will remain open during these closures. These closures are necessary for the continued steel repair as part of the NYCDOT Roosevelt Avenue Bridge rehabilitation project."}, "updated": {"from": false, "to": true}}

### missing (showing 3 of 3)
- `a248e492c0c76c2a` {"page": "weekly", "kind": "closure", "section": "Manhattan", "section_id": "manhattan", "category": null, "name": "Park Avenue Viaduct over East 45th Street", "embargo": null, "updated": true, "streets": {}, "description": "The Park Avenue viaduct between East 40th and East 46th Street will be fully closed, one direction at a time from 12:01am to 5am nightly Monday November 16th, 2026, to Thursday November 19th, 2026, to repair leaking expansion joints as permitted by NYCDOT Bridges."}
- `a8b5b5fa78faa767` {"page": "weekly", "kind": "closure", "section": "Manhattan", "section_id": "manhattan", "category": null, "name": "Southbound Route 9A between West 30th Street and West 26th Street", "embargo": null, "updated": false, "streets": {}, "description": "One out of three southbound lanes will be closed on Saturdays from 12:01am to 9am, and two out of three from 1am to 6am on Sundays. One out of three lanes will be closed from 12:01am to 10am and two of three from 2am to 8am, on Sunday nights. One of three lanes will also be closed 9pm to midnight and two of three lanes closed from 12:01am to 5am Monday mornings to facilitate NYCDEP infrastructure repairs and roadway repaving from October 24th, 2026, to November 16th, 2026."}
- `78821f0c8f9eb72c` {"page": "weekly", "kind": "closure", "section": "Brooklyn", "section_id": "brooklyn", "category": null, "name": "27th Avenue Pedestrian Bridge over the Belt Parkway", "embargo": null, "updated": true, "streets": {}, "description": "As of Friday November 13th, 2026, through Saturday November 14th, 2026, one lane will be closed Friday from 11pm to 6am, and Saturday from 1am to 6am (two lanes-full closure up to 90 minutes), November 14th-November 15th, 2026, alternate rain date Sunday 2am to 7am (two lanes-full closure up to 90 minutes). These closures are necessary to facilitate crane operations for lifting procedures. Variable message signs-(VMS) will be placed in both directions, advising motorists of the upcoming closures and anticipated impacts. The lanes will reopen as soon as is safe to do so. Motorists are advised to allow additional travel time, follow posted detour signs, traffic control devices and use caution when travelling through the work area."}

## Quality
- key check ok: True  |  exact duplicate rows dropped: 0  |  key collisions (different content): 0  |  null-key rows: 0
- missing required: {}
- unparseable dates: {}
- absent configured fields: []

_Note: 'missing' means not present in this snapshot, not that the road reopened._
