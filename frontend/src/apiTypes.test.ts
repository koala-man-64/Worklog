import { expectTypeOf, it } from "vitest";
import type { Answer, Body, Schema } from "./apiTypes";

// Checked by tsc: a route's answer and body are the schemas the backend names for it.
it("reads each route's answer and body from the generated schema", () => {
  expectTypeOf<Answer<"/api/v1/tickets/{ref}">>().toEqualTypeOf<Schema["Ticket"]>();
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
