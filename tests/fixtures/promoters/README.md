# Promoter HTML fixtures

The DOM structure mirrors the public listing pages of each promoter as of
2026-09-06, reduced to the elements the parsers rely on. Artist names, tour
titles, organisers and URL slugs are fictional; cities, venues, dates and
prices are kept realistic so that city recognition and date parsing are
exercised as in production.

| Fixture | Mirrors | Covered behaviour |
| --- | --- | --- |
| knockout-listing.html | https://knockoutprod.net/public/index.php/pl/concert | 32 cards and `rel=next` pagination; no JSON-LD |
| knockout-detail.html | https://knockoutprod.net/public/index.php/pl/concert/detail/1076 | Explicit headliner, support, venue, date, Start and door time; no JSON-LD |
| fource-listing.html | https://fource.pl/pl/events/ | 42 unique occurrences, duplicated for responsive layouts; cancelled and sold-out states |

Knockout's older `/pl/concerts` address loops through redirects. The working
listing URL above was discovered in the public navigation.

Robots snapshots are included alongside the HTML. Production requests always
read current robots rules; snapshots are only used by offline tests.
