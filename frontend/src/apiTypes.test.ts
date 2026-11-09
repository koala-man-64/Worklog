import { expectTypeOf, it } from "vitest";
import type { Answer, Body, ChatMessage, Schema } from "./apiTypes";
import { commands } from "./commands";

// Checked by tsc: a route's answer and body are the schemas the backend names for it.
it("reads each route's answer and body from the generated schema", () => {
  expectTypeOf<Answer<"/api/v1/tickets/{ref}">>().toEqualTypeOf<
    Schema["Ticket"]
  >();
  expectTypeOf<Answer<"/api/v1/tickets">>().toEqualTypeOf<Schema["Tickets"]>();
  expectTypeOf<Answer<"/api/v1/tickets/{ref}", "patch">>().toEqualTypeOf<
    Schema["TicketListed"]
  >();
  expectTypeOf<Answer<"/api/v1/releases", "post">>().toEqualTypeOf<
    Schema["ReleaseFields"]
  >();
  expectTypeOf<Body<"/api/v1/tickets/{ref}", "patch">>().toEqualTypeOf<
    Schema["TicketUpdate"]
  >();
  // A route with no such method answers nothing a page could use.
  expectTypeOf<Answer<"/api/v1/tickets", "delete">>().toBeNever();
});

type Answers = {
  [K in keyof typeof commands]: Awaited<ReturnType<(typeof commands)[K]>>;
};
it("gives every command a route's answer", () => {
  // A command typed with a method its route lacks would answer never.
  type Unanswered = {
    [K in keyof Answers]: [Answers[K]] extends [never] ? K : never;
  }[keyof Answers];
  expectTypeOf<Unanswered>().toBeNever();
  expectTypeOf<Answers["reviseRoadmap"]>().toEqualTypeOf<
    Schema["SavedRoadmap"]
  >();
  expectTypeOf<Answers["upsertEntry"]>().toEqualTypeOf<Schema["DailyEntry"]>();
});

it("sees a transcript message's kinds as one shape", () => {
  // Each kind's own fields, with the types the kinds that have them give.
  expectTypeOf<ChatMessage["kind"]>().toEqualTypeOf<
    "user" | "assistant" | "agent" | "tools"
  >();
  expectTypeOf<ChatMessage["authorThread"]>().toEqualTypeOf<
    string | null | undefined
  >();
  expectTypeOf<ChatMessage["tools"]>().toEqualTypeOf<
    Schema["ToolCall"][] | undefined
  >();
  expectTypeOf<ChatMessage["text"]>().toEqualTypeOf<string | undefined>();
});
