"""store.page(): the stale-while-revalidate contract the bottom tabs rely on, exercised in a real page.

Run:  <venv>/bin/pytest qa/e2e/test_store_page.py -q -p no:cacheprovider   (on its own, like every
Playwright module here).
"""
# ruff: noqa: F811  (pytest fixtures imported from smoke_fixtures are re-bound as test parameters)
import pytest

from smoke_fixtures import _results_sink, browser, make_context, pw  # noqa: F401  (pytest fixtures)

# Runs inside the page: page(key, load, render) with a controllable load(); returns what render saw.
HARNESS = """
async ([key, first, second, writeDuringRefresh, fail]) => {
  const seen = [];
  const render = (data, { cached }) => seen.push({ data, cached });
  await store.page(key, () => Promise.resolve(first), render);
  const before = seen.length;
  let release;
  const gate = new Promise((r) => { release = r; });
  const run = store.page(key, () => gate.then(() => { if (fail) throw new Error('boom'); return second; }), render);
  if (writeDuringRefresh) store.afterWrite('/api/transactions/1');
  release();
  let error = null;
  try { await run; } catch (e) { error = { message: e.message, shown: e.shown }; }
  return { first: seen.slice(0, before), second: seen.slice(before), stale: store.pageStale(key), error };
}
"""


@pytest.fixture
def page(make_context):
    _, pg, _ = make_context("qa_tester", "light", "390")
    pg.goto("/settings.html")
    pg.wait_for_function("() => !!window.currentUser")
    return pg


def run(page, key, first, second, write=False, fail=False):
    return page.evaluate(HARNESS, [key, first, second, write, fail])


def test_first_visit_renders_fresh_once_and_an_unchanged_refresh_renders_nothing(page):
    r = run(page, "t-same", {"n": 1}, {"n": 1})
    assert (r["first"], r["second"], r["stale"]) == ([{"data": {"n": 1}, "cached": False}],
                                                    [{"data": {"n": 1}, "cached": True}], False)


def test_a_changed_refresh_renders_the_kept_copy_then_the_fresh_one(page):
    r = run(page, "t-changed", {"n": 1}, {"n": 2})
    assert r["second"] == [{"data": {"n": 1}, "cached": True}, {"data": {"n": 2}, "cached": False}]


def test_a_write_during_the_refresh_wins_over_the_older_result(page):
    r = run(page, "t-write", {"n": 1}, {"n": 2}, write=True)
    assert (r["second"], r["stale"]) == ([{"data": {"n": 1}, "cached": True}], True)


def test_a_failed_refresh_says_whether_a_copy_is_on_screen(page):
    r = run(page, "t-fail", {"n": 1}, None, fail=True)
    assert (r["second"], r["error"]) == ([{"data": {"n": 1}, "cached": True}], {"message": "boom", "shown": True})


def test_any_write_drops_every_kept_copy(page):
    stale = page.evaluate("""async () => {
      await store.page('t-a', () => Promise.resolve(1), () => {});
      await store.page('t-b', () => Promise.resolve(2), () => {});
      const kept = [store.pageStale('t-a'), store.pageStale('t-b')];
      store.afterWrite('/api/budgets');
      return [...kept, store.pageStale('t-a'), store.pageStale('t-b')];
    }""")
    assert stale == [False, False, True, True]
