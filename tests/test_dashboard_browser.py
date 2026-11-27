"""The live owner dashboard and both published sites, served from one event store."""

import json
import re
import threading
from contextlib import ExitStack
from datetime import timedelta
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests import degraded as D
from tests.conftest import Running
from tests.test_browser import JPEG, Catalog, settled, sync_api
from wowstats import gear, gearmode, itemcard, publish, static, talents, ui

ESTIMATE = "Apprentice-Gearholt"
UNKNOWN = "Newcomer-Gearholt"
UNRANKED = "Unranked-Gearholt"


@pytest.fixture(scope="module")
def dashboards(tmp_path_factory, demo_clock):
    tmp = tmp_path_factory.mktemp("dashboards")
    rows = D.base_rows()
    # These splits fall outside the test route. One alt has a logout but
    # no worn items; the other has only splits, so its class is unknown.
    for name in (ESTIMATE, UNKNOWN):
        for r in [x for x in rows if x["feed"] == "wow-level" and x["char"] == D.CHAR][:2]:
            rows.append({**r, "char": name, "source_id": name + r["source_id"], "level": str(int(r["level"]) - 14)})
    logout = next(x for x in rows if x["feed"] == "wow-character" and x.get("class"))
    rows.append({**logout, "char": ESTIMATE, "source_id": ESTIMATE, "level": "10", "class": "MAGE"})
    # Same equipment, no reputation or PvP currency ever recorded.
    for feed in ("wow-character", "wow-gear"):
        r = next(x for x in reversed(rows) if x["feed"] == feed and x["char"] == D.CHAR)
        rows.append({**r, "char": UNRANKED, "source_id": UNRANKED + feed})
    when = logout["_time"]
    rows.extend([{"feed": "wow-foreverdb", "kind": "pvp", "_time": when, "char": D.CHAR, "pvp_rank": "3"},
                 {"feed": "wow-recorder", "kind": "rep", "_time": when, "char": D.CHAR,
                  "faction_id": "2799", "reaction": "6", "standing": "9000"}])
    route = tmp / "route.json"
    route.write_text(json.dumps({"source": "Test route", "levels": {"22": 600, "23": 900}}))
    with ExitStack() as stack:
        mp = stack.enter_context(pytest.MonkeyPatch.context())
        mp.setenv("ROUTE_REFERENCE_FILE", str(route))
        mp.setenv("WOW_ITEMS", "off")
        catalog = Catalog()
        mp.setattr(itemcard, "shared", lambda: catalog)
        mp.setattr(talents, "icon_bytes", lambda *a: JPEG)
        # Keep a quest path open for this trinket so both characters can
        # compare its alternative sources, even with no rank recorded.
        sources = gear.reference().data["sources"]
        mp.setitem(sources, "1490", [*sources["1490"], {"kind": "pvp", "rank": 3}, {"kind": "pvp", "rank": 5},
                                    {"kind": "reputation", "faction": 2799, "faction_name": "Theramore",
                                     "standing": "Honored"},
                                    {"kind": "reputation", "faction": 890, "faction_name": "Silverwing",
                                     "standing": "Friendly"}])
        env, store = D.store_env("sqlite", rows, tmp)
        stack.callback(store.close)
        env.update(BIND="127.0.0.1", AUTH="none", MODEL3D="off", DEFAULT_CHAR=D.CHAR,
                   PUBLISH_DIR=str(tmp / "site"), ZOGZOG_KEY_FILE=str(tmp / "key"),
                   MODEL3D_CACHE_DIR=str(tmp / "cache"), WOW_STATE_DIR=str(tmp / "state"))
        gearmode.save_pvp(env, D.CHAR, True)
        gearmode.save_pvp(env, UNRANKED, True)
        app = ui.App({**env, "VIEW": "full"})
        app.prebuilt.on = False
        owner = Running(app)
        stack.callback(owner.close)
        assert publish.publish(env)["built"]
        site = static.Site({"SITE_DIR": env["PUBLISH_DIR"], "ZOGZOG_PATH": "/zogzog/"})
        httpd = static.Server(("127.0.0.1", 0), static.make_handler(site))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        stack.callback(httpd.server_close)
        stack.callback(httpd.shutdown)
        base = f"http://127.0.0.1:{httpd.server_port}"
        key = Path(env["ZOGZOG_KEY_FILE"]).read_text().strip()
        yield {"owner": owner.base + "/", "public": base + "/", "friends": base + "/zogzog/",
               "key": key}


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        try:
            engine = pw.chromium.launch()
        except sync_api.Error as e:
            pytest.skip(f"no Chromium: {e}")
        yield engine
        engine.close()


def open_char(page, dashboards, tier, name=D.CHAR):
    url = dashboards[tier] + "?tab=overview&char=" + name
    if tier == "friends":
        url += "#" + dashboards["key"]
    page.goto(url, wait_until="domcontentloaded")
    page.locator("#hero .loading").wait_for(state="detached")
    settled(page, 90)


def splits(page):
    return page.locator("#levels .rows li").evaluate_all("""rows => rows.map(row => [
      row.querySelector('.lv').textContent,
      row.querySelector('.val').firstChild.textContent,
      row.querySelector('.track:not(.route) .fill').style.width,
      row.querySelector('.track.route .fill')?.style.width,
      row.querySelector('.dot.r')?.textContent])""")


@pytest.mark.parametrize("width", [390, 1280])
def test_same_climb_and_gear_from_live_public_and_sealed(dashboards, browser, width):
    common = {}
    for tier in ("owner", "friends", "public"):
        with browser.new_context(viewport={"width": width, "height": 900}) as ctx:
            page = ctx.new_page()
            page.clock.set_fixed_time(D.now())
            paths = []
            errors = []
            page.on("request", lambda r: paths.append(urlsplit(r.url).path))
            page.on("pageerror", lambda e: errors.append(str(e)))
            # The shirt and tabard are named only by the catalog. Interrupt
            # the worn-item batch or the tier's bundle once during first paint.
            if tier == "owner":
                items = re.compile(r"/api/items\?ids=[\d,]*\b2576\b")
            else:
                rel = publish.data_path("items")
                if tier == "friends":
                    rel = publish.sealed_name(publish.unb64u(dashboards["key"]), rel)
                items = dashboards[tier] + rel
            page.route(items, lambda route: route.abort("connectionreset"), times=1)
            with page.expect_request(items, timeout=90000):
                open_char(page, dashboards, tier)
            chart = page.locator("#levels .climb")
            chart.locator("path.line").wait_for(state="attached")
            character_sources = page.locator("#foot-status details")
            if tier == "public":
                assert character_sources.count() == 0
            else:
                character_sources.locator("summary").click()
                assert character_sources.get_by_text("Battle.net", exact=True).count() == 1
                assert ("Auctionator scans" in character_sources.inner_text()) == (tier == "owner")
                main_coverage = character_sources.locator("dl").inner_text()
                character_sources.locator("summary").click()
            values = splits(page)
            assert {v[0]: v[4] for v in values if v[4]} == {"22": "route 10m", "23": "route 15m"}
            assert all(v[1] and v[2] and bool(v[3]) == bool(v[4]) for v in values)
            assert chart.locator("path.route title").text_content() == "Polestar’s route"
            assert "Dashed" in chart.locator(".routekey").inner_text()
            worn = page.locator("#gear li.slot:has(.slotitm) .iname").all_text_contents()
            assert "Whitemane's Chapeau" in worn
            assert {Catalog().card(i)["n"] for i in (2576, 5976)} <= set(worn)
            gear = page.locator("#advice")
            if tier == "owner":
                common.update(splits=values, worn=worn)
                assert page.locator("#gearspec option:checked").inner_text() == "Automatic: Shadow"
            else:
                assert values == common["splits"] and worn == common["worn"]
            if tier == "public":
                assert not page.locator("#tab-home, #tab-journal, #market, #people, .adv-head").count()
                assert not page.locator("#levels .dot.k, #levels .dot.q").count()
            else:
                assert gear.locator(".adv-h").inner_text() == "Upgrades"
                ring = gear.locator('[data-up$="-1980"]').first
                assert "Underworld Band" in ring.text_content()
                assert "24g 55s" in ring.text_content()
                if tier == "owner":
                    page.locator("#gearspec").select_option("holy")
                    settled(page, 60)
                    assert page.locator("#gearspec option:checked").inner_text() == "Holy (healer)"
                else:
                    assert not gear.locator("#gearspec, #gearmode, #gearpvp").count()
                    assert "Scored for Shadow" in gear.locator(".specpicks").inner_text()
                    assert "Leveling weights" in gear.locator(".specpicks").inner_text()
            page.locator("#tab-timeline").click()
            page.locator(".snapbody[data-level]").wait_for()
            settled(page, 60)
            sn = page.locator(".snapbody").get_attribute("data-level")
            assert not page.locator(".m3dtoggle, iframe.m3dframe").count()
            assert not any("model3d" in path for path in paths)
            if tier == "friends":
                key = publish.unb64u(dashboards["key"])
                for args in ((D.CHAR,), (D.CHAR, int(sn))):
                    rel = publish.data_path("model3d", *args)
                    assert "/zogzog/" + publish.sealed_name(key, rel) not in paths
            page.locator("#tab-overview").click()
            page.locator("#foot-sources summary").click()
            page.locator("#char").select_option(ESTIMATE)
            settled(page, 60)
            assert page.locator("#foot-sources details[open]").count() == 1
            if tier == "public":
                assert character_sources.count() == 0
            else:
                character_sources.locator("summary").click()
                assert character_sources.locator("dl").inner_text() != main_coverage
            no_route = splits(page)
            assert len(no_route) == 2 and all(v[2] and not v[3] and not v[4] for v in no_route)
            assert not chart.locator("path.route, .routekey").count()
            assert chart.locator("path.line").count() == 1
            assert "thin bar" not in page.locator("#levels .legend").inner_text()
            if tier != "public":
                assert gear.locator(".adv-h").inner_text() == "Gear for your level"
                assert "equipped items are unknown" in gear.locator(".adv-head").inner_text()
                page.locator("#char").select_option(UNKNOWN)
                settled(page, 60)
                assert gear.locator(".adv-h").inner_text() == "Gear advice"
                assert gear.locator(".empty").count() and not gear.locator(".cand").count()
            assert not errors


def test_unavailable_item_cards_settle_on_the_slot_name(dashboards, browser):
    with browser.new_context() as ctx:
        page = ctx.new_page()
        open_char(page, dashboards, "owner")
        requests = []

        def unavailable(route, *, status):
            requests.append(route.request.url)
            if status is None:
                route.abort("connectionreset")
            else:
                route.fulfill(status=status, json={"items": []})

        for item_id, status, attempts in ((900001, None, 2), (900002, 200, 1), (900003, 403, 1)):
            page.route(f"**/api/items?ids={item_id}", partial(unavailable, status=status))
            requests.clear()
            busy = page.evaluate("""async id => {
              const chip = window.wowItem({item_id: id, slot_name: 'Head'});
              chip.id = 'test-item-' + id;
              document.body.append(chip);
              await Promise.resolve();
              return document.documentElement.hasAttribute('data-busy');
            }""", item_id)
            assert busy
            settled(page)
            assert len(requests) == attempts
            assert page.locator(f"#test-item-{item_id} .iname").inner_text() == "Head item, see on Wowhead"


def test_published_footer_with_an_older_page_and_no_character_data(dashboards, browser):
    # The HTML and script are separate requests during a publication.
    def old_page(route):
        response = route.fetch()
        html = re.sub(r'<footer\b.*?</footer>', '<footer id="foot" class="foot"></footer>', response.text(), flags=re.S)
        route.fulfill(response=response, body=html)

    with browser.new_context() as ctx:
        page = ctx.new_page()
        page.route("**/index.html*", old_page)
        page.route("**/data/chars.json", lambda r: r.fulfill(json={"chars": []}), times=1)
        page.goto(dashboards["public"] + "index.html?char=" + ESTIMATE)
        assert page.locator("#hero .nochars h1").inner_text() == "No character yet"
        assert "Data sources" in page.locator("#foot-sources summary").inner_text()
        assert page.locator("#foot-status").inner_text() == ""

        page.route("**/" + publish.data_path("char", ESTIMATE), lambda r: r.fulfill(status=503, json={"error": "offline"}))
        page.reload()
        assert ESTIMATE in page.locator("#hero .err").inner_text()
        assert "offline" in page.locator("#foot-status .warn").inner_text()
        page.locator("#foot-sources summary").click()
        page.locator("#char").select_option(D.CHAR)
        page.locator("#levels .climb path.line").wait_for(state="attached")
        settled(page, 60)
        assert "Character view as of" in page.locator("#foot-status").inner_text()
        assert not page.locator("#foot-status .warn").count()
        assert page.locator("#foot-sources details[open]").count() == 1


def test_a_stale_listing_is_absent_on_both_gear_cards(dashboards, browser):
    # The same saved scan, three days later in the reader's browser.
    for tier in ("owner", "friends"):
        with browser.new_context() as ctx:
            page = ctx.new_page()
            page.clock.set_fixed_time(D.now() + timedelta(days=3))
            open_char(page, dashboards, tier)
            ring = page.locator('#advice [data-up$="-1980"]').first
            assert "Underworld Band" in ring.text_content()
            assert "24g 55s" not in ring.text_content()
            comparison = compare_sources(page, 1980)
            assert "Auction House listing expired" in comparison and "24g 55s" not in comparison


@pytest.mark.parametrize("back_to_first", [False, True])
def test_a_late_character_cannot_replace_the_selection(dashboards, browser, back_to_first):
    with browser.new_context() as ctx:
        page = ctx.new_page()
        held = []

        def delay_first(route):
            if not held:
                held.append(route)
            else:
                route.continue_()

        page.route(re.compile(r"/api/char\?name=" + D.CHAR + "$"), delay_first)
        with page.expect_request(re.compile(r"/api/char\?name=" + D.CHAR + "$")):
            page.goto(dashboards["owner"] + "?tab=overview&char=" + D.CHAR, wait_until="domcontentloaded")
        page.locator("#char").select_option(ESTIMATE)
        page.locator("#advice .adv-h", has_text="Gear for your level").wait_for()
        if back_to_first:
            page.locator("#char").select_option(D.CHAR)
            page.locator("#advice .adv-h", has_text="Upgrades").wait_for()
        # Distinguish the old A answer from the new A answer as well.
        response = held[0].fetch()
        body = response.json()
        body["header"]["name"] = "Superseded"
        body["levels"] = []
        held[0].fulfill(response=response, json=body)
        settled(page, 60)
        expect = D.CHAR if back_to_first else ESTIMATE
        assert page.locator("#char").input_value() == expect
        assert expect.split("-")[0] in page.locator("#hero").inner_text()
        assert len(splits(page)) == (24 if back_to_first else 2)
        assert page.locator("#advice .adv-h").inner_text() == ("Upgrades" if back_to_first else "Gear for your level")


def compare_sources(page, item_id):
    candidate = page.locator(f'#advice [data-up$="-{item_id}"]').first
    candidate.evaluate("""el => {
      for (let p = el.parentNode; p; p = p.parentNode) if (p.tagName === 'DETAILS') p.open = true;
    }""")
    candidate.locator(".candmain .itm").first.click()
    return page.locator(".cmp .cmpwhere").inner_text()


def test_reward_eligibility_in_the_live_and_sealed_comparisons(dashboards, browser):
    for tier in ("owner", "friends"):
        with browser.new_context() as ctx:
            page = ctx.new_page()
            open_char(page, dashboards, tier)
            sources = compare_sources(page, 1490)
            assert "you hold rank 3" in sources and "rank 5 needed, you're rank 3" in sources
            assert "Theramore: Honored reputation" in sources and "Silverwing: Friendly reputation" in sources
            assert ("you're Honored" in sources) == (tier == "owner")
            assert ("not met yet" in sources) == (tier == "owner")
            assert "standing isn't recorded" not in sources
            page.locator("#char").select_option(UNRANKED)
            settled(page, 60)
            sources = compare_sources(page, 1490)
            assert "your PvP rank isn't recorded yet" in sources
            assert ("your standing isn't recorded" in sources) == (tier == "owner")


def test_late_panel_answers_cannot_redraw_a_new_selection(dashboards, browser):
    with browser.new_context() as ctx:
        page = ctx.new_page()
        panels = {"gear", "loot", "dungeons", "talents", "people", "meta", "plan", "nudge",
                  "overview", "analysis", "timeline", "journal"}
        # Let the network finish. Release the first character's responses to
        # the application only after the other character's panels are drawn.
        page.add_init_script(f"const firstChar = {json.dumps(D.CHAR)}, panels = {json.dumps(sorted(panels))};" + """{
          const fetch = window.fetch;
          window.held = new Map();
          window.fetch = async (...args) => {
            const url = new URL(args[0], location.href);
            const response = await fetch(...args);
            const panel = url.pathname.replace('/api/', '');
            if ((url.searchParams.get('name') || url.searchParams.get('char')) === firstChar && panels.includes(panel)) {
              const body = await response.text();
              await new Promise(resolve => {
                if (!held.has(panel)) held.set(panel, []);
                held.get(panel).push(resolve);
                document.documentElement.dataset.held = held.size;
              });
              if (['gear', 'journal', 'loot', 'people', 'overview', 'timeline'].includes(panel))
                return new Response('{"error":"superseded panel"}', {status: 500});
              return new Response(body, {status: response.status, headers: response.headers});
            }
            return response;
          };
        }""")
        page.goto(dashboards["owner"] + "?tab=overview&char=" + D.CHAR, wait_until="domcontentloaded")
        page.locator("#gear .doll").wait_for()
        for tab in ("home", "timeline", "journal"):
            page.locator("#tab-" + tab).click()
        page.locator(f'html[data-held="{len(panels)}"]').wait_for(state="attached", timeout=60000)
        assert set(page.evaluate("Array.from(held.keys())")) == panels
        page.locator("#char").select_option(ESTIMATE)
        page.locator("#tab-overview").click()
        page.locator("#advice .adv-h", has_text="Gear for your level").wait_for()
        for tab in ("home", "timeline", "journal"):
            page.locator("#tab-" + tab).click()
        page.locator(".snapbody[data-level]").wait_for(state="attached")
        page.wait_for_load_state("networkidle")
        selector = "#home, #advice, #timeline, #journal, #loot, #people-runs, #dungeons, #talents, #planstatus"
        before = page.locator(selector).all_text_contents()
        page.evaluate("held.forEach(replies => replies.forEach(release => release()))")
        settled(page, 60)
        assert page.locator(selector).all_text_contents() == before
        assert "superseded panel" not in page.locator("body").text_content()


def test_private_plan_panel_shows_dungeon_forecast_errors(dashboards, browser):
    with browser.new_context(viewport={"width": 390, "height": 844}) as ctx:
        page = ctx.new_page()
        report = {"reviews": [{}], "by_basis": {"modeled": {"n": 2, "mean_absolute_error_s": 180,
                                                            "mean_absolute_error_xp": 800},
                                                "personal_runs": {"n": 1, "mean_absolute_error_s": 60,
                                                                  "mean_absolute_error_xp": 250}},
                  "counts": {"pending": 1, "incomplete": 2, "ambiguous": 0, "abandoned": 1}}
        page.route("**/api/plan?*", lambda r: r.fulfill(json={"available": True, "dungeon_forecasts": report,
                                                           "gold": "2g", "tiers": []}))
        open_char(page, dashboards, "owner")
        text = page.locator("#planstatus").text_content()
        assert "Modeled: 2 matched trips. Mean absolute error 3m and 800 XP." in text
        assert "Personal runs: 1 matched trip. Mean absolute error 1m and 250 XP." in text
        assert "1 pending, 2 incomplete, 0 ambiguous, 1 abandoned." in text
