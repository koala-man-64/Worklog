import { expect, test, type Locator, type Page } from "@playwright/test";

async function commands(page: Page, url: string) {
  const origin = new URL(url).origin;
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Today", exact: true }),
  ).toBeVisible();
  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  expect(boot.ok(), await boot.text()).toBeTruthy();
  const { csrfToken } = await boot.json();
  return async (path: string, data: unknown, token?: string) => {
    const response = await page.request.post("/api/v1" + path, {
      headers: {
        "Idempotency-Key": crypto.randomUUID(),
        ...(token
          ? { Authorization: "Bearer " + token }
          : { Origin: origin, "X-CSRF-Token": csrfToken }),
      },
      data,
    });
    expect(
      response.ok(),
      `POST ${path}: ${await response.text()}`,
    ).toBeTruthy();
    return response.json();
  };
}

const panel = (page: Page) =>
  page.getByRole("region", { name: "Dependencies", exact: true });
const linkRow = (section: Locator, text: string) =>
  section.locator(".dependency-row").filter({ hasText: text });

async function pickPrerequisite(
  section: Locator,
  kind: "ticket" | "repository",
  name: string,
) {
  await section.getByRole("button", { name: "Add prerequisite" }).click();
  await section.getByLabel("Prerequisite type").selectOption(kind);
  await section.getByRole("button", { name: "Find prerequisite" }).click();
  await section
    .getByRole("textbox", { name: "Prerequisite search" })
    .fill(name);
  await section
    .getByRole("list", { name: "Prerequisite matches" })
    .getByRole("button", { name, exact: kind === "repository" })
    .click();
}

test("links a step to a ticket on another roadmap and follows ticket progress in both directions", async ({
  page,
  baseURL,
}) => {
  const write = await commands(page, baseURL!);
  const suffix = crypto.randomUUID().slice(0, 8);
  const stepId = crypto.randomUUID();
  const stepTitle = "Adopt the publication " + suffix;
  const ticketTitle = "Publish the package " + suffix;
  const publication = await write("/roadmaps", {
    title: "Publication " + suffix,
  });
  const rollout = await write("/roadmaps", {
    title: "Rollout " + suffix,
    steps: [{ id: stepId, title: stepTitle }],
  });
  const item = await write("/work-items", {
    title: "Publish " + suffix,
    roadmapId: publication.id,
  });
  const ticket = await write("/tickets", {
    title: ticketTitle,
    itemId: item.id,
    resolution: "Published the build after review",
  });

  await page.goto(`/#roadmaps/${rollout.id}/${stepId}`);
  await pickPrerequisite(panel(page), "ticket", ticketTitle);
  await panel(page)
    .getByRole("button", { name: "Create dependency", exact: true })
    .click();
  const prerequisite = linkRow(panel(page), ticketTitle);
  await expect(prerequisite.locator(".dependency-state")).toHaveText(
    /waiting/i,
  );
  await prerequisite
    .getByRole("link", { name: ticketTitle, exact: true })
    .click();
  await expect(page).toHaveURL(new RegExp(`#tickets/${ticket.id}$`));
  const reverse = linkRow(panel(page), stepTitle);
  await expect(
    reverse.getByRole("link", { name: stepTitle, exact: true }),
  ).toBeVisible();

  const status = page.getByRole("combobox", { name: "Status", exact: true });
  await status.selectOption("done");
  await expect(reverse.locator(".dependency-state")).toHaveText(/satisfied/i);
  await reverse.getByRole("link", { name: stepTitle, exact: true }).click();
  await expect(prerequisite.locator(".dependency-state")).toHaveText(
    /satisfied/i,
  );
  await prerequisite
    .getByRole("link", { name: ticketTitle, exact: true })
    .click();
  await expect(page).toHaveURL(new RegExp(`#tickets/${ticket.id}$`));
  await expect(reverse.locator(".dependency-state")).toHaveText(/satisfied/i);
  await status.selectOption("open");
  await expect(reverse.locator(".dependency-state")).toHaveText(/waiting/i);
  await page.goto(`/#roadmaps/${rollout.id}/execution`);
  const executionStep = page
    .getByRole("region", { name: "Unassigned leaves" })
    .locator(".exec-step-item")
    .filter({ hasText: stepTitle });
  await expect(executionStep.locator(".dependency-count")).toHaveText(/1 open/);
});

test("confirms a delivery condition alongside a repo catalog link and reflects an agent's own wait", async ({
  page,
  baseURL,
}) => {
  const write = await commands(page, baseURL!);
  const suffix = crypto.randomUUID().slice(0, 8);
  const publisherName = "Package publisher " + suffix;
  const consumerName = "Package consumer " + suffix;
  const publisher = await write("/repositories", { name: publisherName });
  const consumer = await write("/repositories", {
    name: consumerName,
    upstreamIds: [publisher.id],
  });

  await page.goto(`/#repositories/${consumer.id}`);
  await expect(
    linkRow(panel(page), publisherName).locator(".dependency-state"),
  ).toHaveText(/linked/i);
  await pickPrerequisite(panel(page), "repository", publisherName);
  const condition = "Publish the signed package " + suffix;
  await panel(page).getByPlaceholder("What needs to happen?").fill(condition);
  await panel(page)
    .getByRole("button", { name: "Create dependency", exact: true })
    .click();
  const delivery = linkRow(panel(page), condition);
  await expect(delivery.locator(".dependency-state")).toHaveText(/waiting/i);
  await delivery.getByRole("button", { name: "Confirm resolved" }).click();
  await expect(delivery.locator(".dependency-state")).toHaveText(/satisfied/i);

  const reporter = await write("/credentials", {
    label: "Consumer agent " + suffix,
  });
  const upstream = await write("/credentials", {
    label: "Publisher agent " + suffix,
  });
  const handoff = "Send the package digest " + suffix;
  const wait = await write(
    "/dependencies/waits",
    {
      prerequisite: { type: "agent", id: upstream.agentId },
      context: { type: "repository", id: consumer.id },
      condition: handoff,
    },
    reporter.token,
  );
  await page.goto("/#live");
  const notice = linkRow(page.locator(".live-waits"), handoff);
  await expect(
    notice.getByRole("link", { name: consumerName, exact: true }),
  ).toBeVisible();

  await write(
    "/agent-runs",
    {
      displayName: "Continue independent integration",
      clientRunKey: "dependency-browser-" + suffix,
    },
    reporter.token,
  );
  await page.reload();
  await expect(notice).toBeVisible();
  await write(
    `/dependencies/${wait.id}/confirm`,
    { revision: wait.revision },
    reporter.token,
  );
  await page.reload();
  await expect(notice).toHaveCount(0);
});
