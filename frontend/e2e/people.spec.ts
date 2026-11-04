import { expect, test, type Page } from "@playwright/test";

/** Types into a person's field, leaves it, and waits for the state its save reports. */
async function setField(
  page: Page,
  label: string,
  value: string,
  state: string | RegExp = "Saved",
) {
  const field = page.getByRole("textbox", { name: label, exact: true });
  await field.fill(value);
  await field.press("Tab");
  await expect(
    field.locator("xpath=ancestor::section[1]").getByRole("status"),
  ).toHaveText(state);
}

/** Ties what a person's page search finds, by the picker's button and search label. */
async function tie(page: Page, button: string, search: string, name: string) {
  await page.getByRole("button", { name: button, exact: true }).click();
  await page
    .getByRole("textbox", { name: search + " search", exact: true })
    .fill(name);
  await page
    .getByRole("list", { name: search + " matches", exact: true })
    .getByRole("button", { name: new RegExp(name) })
    .click();
  await expect(
    page.getByRole("textbox", { name: "Role on " + name, exact: true }),
  ).toBeVisible();
}

/** The section a heading starts, such as a page's "People". */
function section(page: Page, heading: RegExp) {
  return page.getByRole("heading", { name: heading }).locator("xpath=..");
}

test("a person keeps their details and how to reach them, and is tied to work, a roadmap and a repository", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  const suffix = crypto.randomUUID();
  const name = "Dana Kim " + suffix;
  const email = `dana.${suffix}@contoso.com`;
  const workTitle = "Roll out ingest alerts " + suffix;
  const roadmapTitle = "Ingest reliability " + suffix;
  const repoName = "ingest-" + suffix;
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
  const work = await write("/work-items", { title: workTitle });
  await write("/roadmaps", { title: roadmapTitle });
  const repository = await write("/repositories", {
    name: repoName,
    url: `https://github.com/people/${suffix}`,
  });

  // Add the person from the list, with a title and team.
  await page
    .getByRole("navigation", { name: "Main navigation" })
    .getByRole("link", { name: "People", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Person name", exact: true })
    .fill(name);
  await page
    .getByRole("textbox", { name: "Person title", exact: true })
    .fill("Site Reliability Engineer");
  await page
    .getByRole("textbox", { name: "Person team", exact: true })
    .fill("Platform");
  await page.getByRole("button", { name: "Add person", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Filter people by text", exact: true })
    .fill(suffix);
  const row = page
    .locator(".person-row")
    .filter({ has: page.getByRole("link", { name, exact: true }) });
  await expect(
    row.getByText(
      "Site Reliability Engineer · Platform · 0 work items · 0 roadmaps · 0 repositories",
    ),
  ).toBeVisible();

  // On their page, every field saves on its own; an address that would not work is refused.
  await row.getByRole("link", { name, exact: true }).click();
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
  await setField(page, "Email", "dana", /^Not saved: Email must be an address/);
  await setField(page, "Email", email);
  await setField(page, "Phone", "+1 (312) 555-0100 ext. 12");
  await setField(page, "Chat", "@dana on Slack");
  await setField(page, "Location or time zone", "Chicago (US Central)");
  await setField(page, "Description", "Runs the ingest pipeline.");
  await page.reload();
  await expect(
    page.getByRole("textbox", { name: "Email", exact: true }),
  ).toHaveValue(email);
  await expect(
    page.getByRole("textbox", { name: "Description", exact: true }),
  ).toHaveValue("Runs the ingest pipeline.");
  const reach = page.getByRole("group", { name: "How to reach " + name });
  await expect(reach.getByRole("link", { name: email })).toHaveAttribute(
    "href",
    "mailto:" + email,
  );
  await expect(
    reach.getByRole("link", { name: "+1 (312) 555-0100 ext. 12" }),
  ).toHaveAttribute("href", "tel:+13125550100");
  await expect(reach.getByText("Chicago (US Central)")).toBeVisible();

  // Tie work, a roadmap and a repository from the person's page, each with a role.
  await tie(page, "Tie work item", "Work to tie", workTitle);
  await tie(page, "Tie roadmap", "Roadmap to tie", roadmapTitle);
  await tie(page, "Tie repository", "Repository to tie", repoName);
  const role = page.getByRole("textbox", {
    name: "Role on " + repoName,
    exact: true,
  });
  await role.fill("Maintainer");
  await role.press("Tab");
  await page.reload();
  await expect(role).toHaveValue("Maintainer");

  // The repository's page and the work item's page show them, with their title and team.
  await page.goto("/#repositories/" + repository.id);
  const people = section(page, /^People/);
  await expect(people.getByRole("link", { name, exact: true })).toBeVisible();
  await expect(
    people.getByText("Site Reliability Engineer · Platform", { exact: true }),
  ).toBeVisible();
  await expect(
    people.getByRole("textbox", { name: "Role for " + name, exact: true }),
  ).toHaveValue("Maintainer");
  await page.goto("/#work/" + work.id);
  await expect(
    section(page, /^People/).getByRole("link", { name, exact: true }),
  ).toBeVisible();

  // Unlinking from the repository's side takes the tie off the person's page.
  await page.goto("/#repositories/" + repository.id);
  await people
    .getByRole("button", { name: "Unlink " + name, exact: true })
    .click();
  await expect(people.getByRole("link", { name, exact: true })).toHaveCount(0);
  await page.goto("/#people");
  await page
    .getByRole("textbox", { name: "Filter people by text", exact: true })
    .fill(suffix);
  await expect(
    row.getByText(
      "Site Reliability Engineer · Platform · 1 work item · 1 roadmap · 0 repositories",
    ),
  ).toBeVisible();
});
