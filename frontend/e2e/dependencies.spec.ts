import { expect, test } from "@playwright/test";

test("shows open prerequisite counts and keeps the normal step progress route available", async ({
  page,
  baseURL,
}) => {
  const origin = new URL(baseURL!).origin;
  const suffix = crypto.randomUUID();
  const roadmapTitle = "Dependency progress " + suffix;
  const stepId = crypto.randomUUID();
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Today", exact: true })).toBeVisible();

  const boot = await page.request.post("/api/v1/session/bootstrap", {
    headers: { Origin: origin, "X-Worklog-Bootstrap": "1" },
    data: {},
  });
  const { csrfToken } = await boot.json();
  async function write(method: "POST" | "PATCH", path: string, data: unknown) {
    const response = await page.request.fetch("/api/v1" + path, {
      method,
      headers: {
        "Idempotency-Key": crypto.randomUUID(),
        Origin: origin,
        "X-CSRF-Token": csrfToken,
      },
      data,
    });
    expect(response.ok(), `${method} ${path}: ${await response.text()}`).toBeTruthy();
    return response.json();
  }

  const roadmap = await write("POST", "/roadmaps", {
    title: roadmapTitle,
    steps: [{ id: stepId, title: "Consume publication", position: 0 }],
  });
  const prerequisite = await write("POST", "/work-items", {
    title: "Publish dependency artifact " + suffix,
  });
  const dependency = await write("POST", "/dependencies", {
    dependent: { type: "step", id: stepId },
    prerequisite: { type: "item", id: prerequisite.id },
    condition: "The artifact is published",
  });
  expect(dependency.state).toBe("waiting");

  const beforeProgress = await page.request.get(`/api/v1/dependencies/${dependency.id}`);
  expect((await beforeProgress.json()).state).toBe("waiting");

  // Exercise the established progress contract while its prerequisite is still open.
  const step = await (await page.request.get(`/api/v1/steps/${stepId}`)).json();
  const progress = await write("PATCH", `/steps/${stepId}/progress`, {
    revision: step.revision,
    status: "doing",
  });
  expect(progress.status).toBe("doing");
  const afterProgress = await page.request.get(`/api/v1/dependencies/${dependency.id}`);
  expect((await afterProgress.json()).state).toBe("waiting");

  await page.goto(`/#roadmaps/${roadmap.id}/${stepId}`);
  await expect(page.getByRole("heading", { name: "Depends on", exact: true })).toBeVisible();

  await page.goto(`/#roadmaps/${roadmap.id}/outline`);
  await expect(page.getByText("1 open dependency", { exact: true })).toBeVisible();

  await page.goto(`/#roadmaps/${roadmap.id}`);
  await expect(page.getByRole("heading", { name: /Open dependencies/ })).toBeVisible();

  await page.goto(`/#roadmaps/${roadmap.id}/execution`);
  await expect(page.getByRole("heading", { name: roadmapTitle, exact: true })).toBeVisible();
  const metric = page
    .locator(".exec-summary > div")
    .filter({ has: page.getByText("Open dependencies", { exact: true }) });
  await expect(metric.locator("dd")).toHaveText("1");

  const summary = await page.request.get(
    `/api/v1/dependencies/roadmaps/${roadmap.id}`,
  );
  expect(await summary.json()).toEqual({ openByStep: { [stepId]: 1 } });
});
