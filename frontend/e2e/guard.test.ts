import { expect, it } from "vitest";
import { checkTarget } from "./guard";

it("runs against the server the suite starts itself", () => {
  expect(() => checkTarget({})).not.toThrow();
  expect(() => checkTarget({ WORKLOG_PORT: "8891" })).not.toThrow();
});

it("refuses a server it did not start unless told it is disposable", () => {
  expect(() =>
    checkTarget({ WORKLOG_BASE_URL: "http://127.0.0.1:8891" }),
  ).toThrow(/WORKLOG_E2E_DISPOSABLE=1/);
  expect(() =>
    checkTarget({
      WORKLOG_BASE_URL: "http://127.0.0.1:8891",
      WORKLOG_E2E_DISPOSABLE: "yes",
    }),
  ).toThrow(/WORKLOG_E2E_DISPOSABLE=1/);
  expect(() =>
    checkTarget({
      WORKLOG_BASE_URL: "http://127.0.0.1:8891",
      WORKLOG_E2E_DISPOSABLE: "1",
    }),
  ).not.toThrow();
});

it("always refuses port 8877, where the personal instance runs", () => {
  for (const url of [
    "http://127.0.0.1:8877",
    "http://localhost:8877/",
    "http://127.0.0.1:8877/#today",
  ])
    expect(() =>
      checkTarget({ WORKLOG_BASE_URL: url, WORKLOG_E2E_DISPOSABLE: "1" }),
    ).toThrow(/8877/);
});
