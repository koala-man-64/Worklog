/**
 * What the backend's routes take and answer, from its OpenAPI schema (generatedApi.ts, which
 * `npm run generate:api` writes from docs/openapi.json). Nothing here describes a shape by hand:
 * each name is a generated schema, or a route's answer, under the name the pages use for it.
 */
import type { components, paths } from "./generatedApi";

export type Schema = components["schemas"];
type Method = "get" | "post" | "put" | "patch" | "delete";
type Operation<P extends keyof paths, M extends Method> = NonNullable<paths[P][M]>;

// A method a route lacks is `never` here; conditional types infer `unknown` from `never`, so the
// helpers below check for it first.
type Lacks<P extends keyof paths, M extends Method> = [
  Operation<P, M>,
] extends [never]
  ? true
  : false;

/** A route's answer: what it sends with 200; never for a method the route lacks. */
export type Answer<P extends keyof paths, M extends Method = "get"> =
  Lacks<P, M> extends true
    ? never
    : Operation<P, M> extends {
          responses: { 200: { content: { "application/json": infer A } } };
        }
      ? A
      : never;

/** What a route takes as its JSON body; never for a method the route lacks. */
export type Body<P extends keyof paths, M extends Method> =
  Lacks<P, M> extends true
    ? never
    : Operation<P, M> extends {
          requestBody?: { content: { "application/json": infer B } };
        }
      ? B
      : never;

/** Who wrote a reminder, a ticket or a comment; the person has no avatar. */
export type Author = Schema["Author"];
/** One change in a record's own history, without the records: /history has those. */
export type Change = Schema["Change"];

// Tickets.
export type Ticket = Schema["TicketListed"];
export type TicketDetail = Schema["Ticket"];
export type TicketComment = Schema["TicketComment"];
export type TicketKind = Ticket["kind"];
export type TicketPriority = Ticket["priority"];

// Releases.
export type ReleaseRow = Schema["ReleaseListed"];
export type ReleaseDetail = Schema["Release"];
export type ReleaseStatus = ReleaseRow["status"];
export type ReleaseCounts = Schema["ReleaseCounts"];
export type ReleaseCheck = Schema["ReleaseCheck"];
export type CheckKind = ReleaseCheck["kind"];
export type ReleaseTarget = Schema["ReleaseHolding"]["targetType"];
export type ReleaseItem = Schema["ReleaseItem"];
export type ReleaseRoadmap = Schema["ReleaseRoadmap"];
export type ReleaseStep = Schema["ReleaseStep"];
export type ReleaseChange = Schema["ReleaseChange"];
export type ReleaseEvent = Schema["ReleaseHistoryEntry"];

// Reminders.
export type Reminder = Schema["ReminderListed"];
/** A reminder with its notes, as a create or an edit returns it. */
export type ReminderRecord = Schema["ReminderRecord"];
/** A reminder with its notes and history, as its own page reads it. */
export type ReminderDetail = Schema["Reminder"];
export type ReminderTarget = Schema["ReminderTarget"];
export type ReminderTargetType = ReminderTarget["type"];

// Resources.
export type Resource = Schema["ResourceListed"];
export type ResourcePage = Schema["ResourcePage"];
export type ResourceDetail = Schema["Resource"];

// Repositories and coordination.
/** A repository as other records embed it. */
export type Repository = Schema["Repository"];
/** A repository as its list and an address's resolution show it, with its own discussion. */
export type DiscussedRepository = Schema["DiscussedRepository"];
export type RepositoryDetail = Schema["RepositoryDetail"];
export type RepositoryRegistration = Schema["EnsuredRepository"];
export type RepositoryResolution = Schema["RepositoryResolution"];
export type AgentRun = Schema["AgentRun"];
export type Participation = Schema["Participation"];
export type ParticipationRepository = Schema["ParticipationRepository"];
export type CoordinationUpdate = Schema["CoordinationUpdate"];

// The agent registry.
export type AgentSummary = Schema["AgentListed"];
export type AgentDetail = Schema["Agent"];
export type RegistryWork = Schema["AgentWork"];
export type RegistryRole = Schema["AgentRole"];
export type TieReason = Schema["TiedWorkItem"]["via"][number];
/** A tracked chat whose chat ID the agent reported. */
export type TiedChat = Schema["TiedChat"];
export type RoadmapTie = Schema["RoadmapTie"];
export type RoadmapWorker = Schema["RoadmapWorker"];
export type AgentActivity = Schema["AgentActivity"]["items"][number];

// Agent ties.
export type AgentTie = Schema["AgentTie"];
export type TiedAgent = Schema["TieAgent"];
/** A tied record in one shape: what every kind has, and each kind's own fields where it has them. */
export type TieTargetView = Schema["RecordSummary"] &
  Partial<
    Omit<
      Schema["StepSummary"] &
        Schema["RepositorySummary"] &
        Schema["TicketSummary"] &
        Schema["AgentSummary"],
      keyof Schema["RecordSummary"]
    >
  >;

// A chat's outbox.
export type Outbox = Schema["ChatMessages"];
export type OutboxRun = Schema["ChatRecipient"];
export type OutboxRecipient = Schema["BoundRecipient"];
export type OutboxDelivery = Schema["ChatDelivery"];
export type OutboxMessage = Schema["ChatMessageSent"];

// Discussions.
export type DiscussionContextType = Schema["ContextTag"]["type"];
/** A thread's tag in one shape: what every kind has, and each kind's own fields where it has them. */
export type DiscussionContext = Schema["ContextTag"] &
  Partial<
    Omit<
      Schema["StepContextTag"] &
        Schema["DecisionContextTag"] &
        Schema["RepositoryContextTag"] &
        Schema["TicketContextTag"] &
        Schema["ReleaseContextTag"],
      keyof Schema["ContextTag"]
    >
  >;
export type DiscussionSummary = Schema["DiscussionSummary"];
export type DiscussionList = Schema["Discussions"];
export type DiscussionDetail = Schema["DiscussionThread"];
export type DiscussionMessage = Schema["DiscussionMessage"];
export type DiscussionParticipant = Schema["Participant"];
export type DiscussionParticipants = Schema["Participants"];
export type AvatarCatalog = Schema["Avatars"];
export type AcknowledgementRequest = Schema["AcknowledgementRequest"];
export type AcknowledgementTarget = Schema["AcknowledgementTarget"];
export type AcknowledgementReceipt = Schema["AcknowledgementReceipt"];
export type AcknowledgementMode = AcknowledgementRequest["mode"];
export type AcknowledgementState = AcknowledgementRequest["state"];
export type DiscussionRole = Schema["DiscussionRole"];
export type DiscussionRoles = Schema["DiscussionRoles"];
export type DiscussionResume = Schema["DiscussionResume"];
export type SearchResults = Schema["SearchResults"];
