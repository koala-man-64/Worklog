import { expect, it } from "vitest";
import {
  clockIn,
  inAnHour,
  nextDay,
  targetHref,
  toTomorrow,
  whenLabel,
} from "./reminderTime";

// 05:30 UTC on 6 November 2026: still the 5th in Chicago, the afternoon in Tokyo.
const MOMENT = Date.parse("2026-11-06T05:30:00Z");

it("reads the date and time of a moment on the clock of the Worklog timezone", () => {
  expect(clockIn("America/Chicago", MOMENT)).toEqual({
    date: "2026-11-05",
    time: "23:30",
  });
  expect(clockIn("Asia/Tokyo", MOMENT)).toEqual({
    date: "2026-11-06",
    time: "14:30",
  });
  // Midnight is 00:00, never 24:00.
  expect(clockIn("UTC", Date.parse("2026-11-06T00:00:00Z"))).toEqual({
    date: "2026-11-06",
    time: "00:00",
  });
});

it("snoozes an hour by the clock, across a change of day and of daylight saving time", () => {
  expect(inAnHour("America/Chicago", MOMENT)).toEqual({
    date: "2026-11-06",
    time: "00:30",
  });
  // 01:30 on 8 March 2026 in Chicago is an hour before clocks skip 02:00 to 03:00.
  expect(
    inAnHour("America/Chicago", Date.parse("2026-03-08T07:30:00Z")),
  ).toEqual({ date: "2026-03-08", time: "03:30" });
});

it("snoozes past the hour a clock is set back, which an hour on would read again", () => {
  // 01:30 CDT on 1 November 2026: an hour on is 01:30 CST, which reads no later.
  expect(
    inAnHour("America/Chicago", Date.parse("2026-11-01T06:30:00Z")),
  ).toEqual({ date: "2026-11-01", time: "02:30" });
  // In the repeated hour itself, an hour on reads later, as on any other day.
  expect(
    inAnHour("America/Chicago", Date.parse("2026-11-01T07:30:00Z")),
  ).toEqual({ date: "2026-11-01", time: "02:30" });
  expect(
    inAnHour("America/Chicago", Date.parse("2026-11-01T05:59:00Z")),
  ).toEqual({ date: "2026-11-01", time: "01:59" });
});

it("snoozes to tomorrow at the reminder's own time, or all day", () => {
  expect(toTomorrow({ time: "09:15" }, "America/Chicago", MOMENT)).toEqual({
    date: "2026-11-06",
    time: "09:15",
  });
  expect(toTomorrow({ time: null }, "Asia/Tokyo", MOMENT)).toEqual({
    date: "2026-11-07",
    time: null,
  });
});

it("counts days across months, leap days and years", () => {
  expect(nextDay("2026-11-30")).toBe("2026-12-01");
  expect(nextDay("2028-02-28")).toBe("2028-02-29");
  expect(nextDay("2026-12-31")).toBe("2027-01-01");
});

it("names today and tomorrow, and a time or the whole day", () => {
  expect(whenLabel({ date: "2026-11-06", time: "14:25" }, "2026-11-06")).toBe(
    "Today · 14:25",
  );
  expect(whenLabel({ date: "2026-11-07", time: null }, "2026-11-06")).toBe(
    "Tomorrow · All day",
  );
  const later = whenLabel({ date: "2026-11-20", time: null }, "2026-11-06");
  expect(later).toMatch(/· All day$/);
  expect(later).not.toMatch(/2026/);
  expect(
    whenLabel({ date: "2027-01-04", time: "08:00" }, "2026-11-06"),
  ).toMatch(/2027/);
});

it("links each kind of record to its own page", () => {
  const id = "11111111-1111-4111-8111-111111111111";
  expect(
    ["item", "ticket", "roadmap", "person", "repository", "chat"].map((type) =>
      targetHref({ type: type as never, id }),
    ),
  ).toEqual(
    ["work", "tickets", "roadmaps", "people", "repositories", "chats"].map(
      (page) => `#${page}/${id}`,
    ),
  );
});
