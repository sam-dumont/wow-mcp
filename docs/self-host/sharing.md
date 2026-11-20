# Share your page

**For:** self-hosters who want friends, or anyone, to see their characters. **At the end:**
the public site and the sealed friends' site build by themselves after each play session, and
you know how to hand out, change and switch off each link.

Your full view shows everything and sits behind your login. Others get static copies: plain
files, rebuilt by the `publish` service and served by the `site` service on port 8081. Two of
them, each cut down to its own allow list:

| Site | Address | Shows | Who |
|---|---|---|---|
| Public | `/` | The character card, gear, the level climb, dungeons cleared, each past level's gear | Anyone with the address |
| Sealed | `/zogzog/#<key>` | The public site plus the journal, zones, guide steps, gear advice, prices, loot, deaths as counts per zone and level | Anyone with the whole link |

Neither ever shows other players, what killed you, or where the character is right now. The
exact list per tier: [Privacy tiers](../explanation/privacy.md).

## Turn them on

They're on in the compose kit: `docker compose up -d` starts `publish` and `site`. `publish`
checks for new events every 10 minutes (`PUBLISH_INTERVAL`), rebuilds after each play session
and at least once a day, and swaps the new build in at once. A failed build leaves the last
good one in place.

Behind a reverse proxy, set `SITE_URL=https://wow.example.org` in `.env` so printed links use
that address ([Put it behind a reverse proxy](reverse-proxy.md)).

## The sealed link

Every data file of `/zogzog/` is encrypted (AES-256-GCM). The key is the part of the link
after `#`, which browsers never send to a server: it stays out of requests, logs and proxies.
Without it, or with a wrong one, the page says "Nothing here."

Print the link (it's never written to a log):

```sh
docker compose run --rm publish zogzog-key link
```

A friend shouldn't have it any more, or you lost track of who does? Make a new key. Every old
link stops working:

```sh
docker compose run --rm publish zogzog-key rotate
```

Don't want a sealed site at all: `ZOGZOG_PATH=` (set, empty) in `.env`, then
`docker compose up -d`. Only the public site is built.

## The help page for friends

On a shared install, `publish` can also write a manual for your friends at
`/help/#<key>`: the install steps, the page tour and how to connect their AI, with clips. It
holds no character data and has its own key, so it opens nothing else. Make the key once and
point `HELP_KEY_FILE` at it on `publish`:

```sh
python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).rstrip(b'=').decode())"
```

No key file, no help page.

## Pictures of past levels (optional)

The public and sealed sites show each past level's gear. With the renderer, they also show
what the character looked like at that level, drawn with Wowhead's 3D viewer in a headless
Chromium. It's its own image (`wow-mcp-render`, or `docker build --target renderer .`), runs
one picture at a time (about 4 seconds each) and peaks at about 850 MB of memory. Give it no
secrets and no route to your stores; it only needs to reach `wow.zamimg.com`. Point
`RENDER_URL` on `publish` at it. Without it, past levels show the 3D model instead.
[Deploy without compose, past levels' pictures](../deploy.md#past-levels-pictures-wow-stats-render)
has the hardening.

## Visitor counts (optional)

With your own [Plausible](https://plausible.io/) and `PLAUSIBLE_DOMAIN` set, the static pages
send one pageview per visit, through your own address: route `/js/script.js` and `/api/event`
on the public name to your Plausible. The pageview carries the path alone (`/` or
`/zogzog/`), never the key or the query. Set it on the ui too and the full view counts into
the same Plausible site ([Deploy without compose, visitor counts](../deploy.md#visitor-counts-plausible)).
Empty: no analytics at all.

## Small machine or no internet

`MODEL3D=off` and `WOW_ITEMS=off` in `.env` stop every download: no 3D view, no item icons
or tooltip cards. The sites still build, from what the events carry.
