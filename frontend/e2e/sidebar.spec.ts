import { expect, test } from "@playwright/test";
import { destinations, sections } from "../src/navigation";

test("every sidebar link stays reachable beside a long roadmap list in a short window", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  const suffix = crypto.randomUUID();
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/");
  // Bootstrap after the page has its session, so both share one CSRF token.
  await expect(
    page.getByRole("heading", { name: "Today", exact: true }),
  ).toBeVisible();
  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  const { csrfToken } = await boot.json();
  async function write(path: string, data: unknown) {
    const response = await page.request.post("/api/v1" + path, {
      headers: {
        "Idempotency-Key": crypto.randomUUID(),
        Origin: origin,
        "X-CSRF-Token": csrfToken,
      },
      data,
    });
    expect(response.ok(), `${path}: ${await response.text()}`).toBeTruthy();
    return response.json();
  }
  const roadmaps: { id: string; title: string; revision: number }[] = [];
  try {
    // Twenty roadmaps fill the Roadmaps list to its cap, so the nav needs more than 720px.
    for (let n = 1; n <= 20; n++)
      roadmaps.push(
        await write("/roadmaps", { title: `Sidebar ${n} ${suffix}` }),
      );
    await page.reload();
    const sidebar = page.getByRole("complementary");
    const nav = sidebar.getByRole("navigation", { name: "Main navigation" });
    await expect(nav.getByRole("link", { name: suffix })).toHaveCount(20);
    const brand = sidebar.getByRole("link", { name: /^w\. worklog/ });
    const brandBox = await brand.boundingBox();
    // Each link comes into view when it takes focus, as it does on Tab. The Roadmaps
    // list is 42vh tall and scrolls by whole pixels, so a link can keep a sub-pixel sliver
    // out of view; 0.95 allows that and nothing more.
    for (const name of [
      ...destinations.map(({ label }) => label),
      ...roadmaps.map(({ title }) => title),
    ]) {
      const link = nav.getByRole("link", { name, exact: true });
      await link.focus();
      await expect(link).toBeInViewport({ ratio: 0.95 });
    }
    // The nav scrolls under the brand, which stays put, and never sideways.
    await nav.getByRole("link", { name: "Guide", exact: true }).focus();
    expect(await brand.boundingBox()).toEqual(brandBox);
    expect(
      await nav.evaluate((element) => {
        const scrollArea = element.parentElement!;
        return scrollArea.scrollWidth - scrollArea.clientWidth;
      }),
    ).toBe(0);
    // The last pages open by mouse too.
    for (const name of ["Settings", "Guide"]) {
      await nav.getByRole("link", { name, exact: true }).click();
      await expect(
        page.getByRole("heading", { level: 1, name, exact: true }),
      ).toBeVisible();
    }
  } finally {
    // Archive them, so later runs on the same data start with a short list.
    for (const { id, revision } of roadmaps)
      await write(`/roadmaps/${id}/archive`, { revision });
  }
});

test("the sidebar groups its pages into Plan, Directory, Agents and System", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");
  const nav = page
    .getByRole("complementary")
    .getByRole("navigation", { name: "Main navigation" });
  expect(sections.map(({ label }) => label)).toEqual([
    "Plan",
    "Directory",
    "Agents",
    "System",
  ]);
  for (const { label, destinations: pages } of sections) {
    // Each section is a group its label names, holding its own pages.
    const group = nav.getByRole("group", { name: label, exact: true });
    await expect(group).toBeVisible();
    for (const { label: name } of pages)
      await expect(
        group.getByRole("link", { name, exact: true }),
      ).toBeVisible();
  }
  // On a phone the stacked sidebar keeps one grid of links, without the labels.
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.locator("#nav-section-directory")).toBeHidden();
  for (const { label } of destinations)
    await expect(
      nav.getByRole("link", { name: label, exact: true }),
    ).toBeVisible();
});
