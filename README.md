# London Blue Plaques

The Bluesky account [@blueplaques.bsky.social](https://bsky.app/profile/blueplaques.bsky.social): a London blue plaque twice a day, from every scheme (English Heritage, the London County Council and Greater London Council, the City of London, the boroughs and local societies), with a photograph from [Wikimedia Commons](https://commons.wikimedia.org/wiki/Category:Blue_plaques_in_London), credited. Not affiliated with any of those bodies.

## How it works

- `blueplaques_harvest.py` walks Commons' "Blue plaques in London" category tree. A plaque is a category of its own ("Piet Mondrian lived here (blue plaque), Camden"); the borough categories it sits under give the third hashtag. Each photo's licence, author, description and coordinates are recorded in `data/plaques.json`.
- `blueplaques_pick.py` shows a model every freely licensed photo of a plaque. It picks the one whose inscription is most readable and transcribes it, line by line and as a line of prose. Code checks that the prose has exactly the transcribed words, in order, before the pick can be posted.
- `blueplaques_post.py` posts the next plaque in a fixed shuffled order: the address, scheme and year from the photo's Commons description, a map link, the photo credit, and the inscription as alt text. It picks on demand if a plaque has no cached pick.

Only CC0, public domain, CC BY and CC BY-SA photos are used, and an attribution photo is used only when its author can be named.

## Running it

```
python3 blueplaques_harvest.py          # about 20 minutes
python3 blueplaques_pick.py --limit 20
python3 blueplaques_post.py --dry-run
python3 test_blueplaques.py
```

The post needs an app password in the Keychain (`security add-generic-password -a blueplaques.bsky.social -s blueplaques-bluesky -w`). The picker uses the Claude token the other scheduled bots use.

The avatar is drawn by `avatar-source.html` in K-Type's "Blue Plaque" typeface, which is licensed and not in this repository.
