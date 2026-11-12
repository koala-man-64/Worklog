/**
 * What the backend's routes take and answer, from its OpenAPI schema (generatedApi.ts, which
 * `npm run generate:api` writes from docs/openapi.json). Nothing here describes a shape by hand:
 * each name is a generated schema, or a route's answer, under the name the pages use for it.
 */
import type { components, paths } from "./generatedApi";

export type Schema = components["schemas"];
type Method = "get" | "post" | "put" | "patch" | "delete";
type RouteOperation<P extends keyof paths, M extends Method> = NonNullable<
  paths[P][M]
>;

// A method a route lacks is `never` here; conditional types infer `unknown` from `never`, so the
// helpers below check for it first.
type Lacks<P extends keyof paths, M extends Method> = [
  RouteOperation<P, M>,
] extends [never]
  ? true
  : false;

/** A route's answer: what it sends with 200; never for a method the route lacks. */
export type Answer<P extends keyof paths, M extends Method = "get"> =
  Lacks<P, M> extends true
    ? never
    : RouteOperation<P, M> extends {
          responses: { 200: { content: { "application/json": infer A } } };
        }
      ? A
      : never;

/** What a route takes as its JSON body; never for a method the route lacks. */
export type Body<P extends keyof paths, M extends Method> =
  Lacks<P, M> extends true
    ? never
    : RouteOperation<P, M> extends {
          requestBody?: { content: { "application/json": infer B } };
        }
      ? B
      : never;

// A union seen as one shape. keyof a union is the fields every member has, which Pick keeps
// with each member's types; a field only some members have is optional, with their types. An
// intersection would not do: members whose kinds are different literals intersect to never.
type AnyKey<T> = T extends unknown ? keyof T : never;
type FieldOf<T, K extends PropertyKey> = T extends unknown
  ? K extends keyof T
    ? T[K]
    : never
  : never;
type Merged<T> = Pick<T, keyof T> & {
  [K in Exclude<AnyKey<T>, keyof T>]?: FieldOf<T, K>;
};

/** Who wrote a ticket or a comment; the person has no avatar. */
export type Author = Schema["Author"];
/** One change in a record's own history, without the records: /history has those. */
export type Change = Schema["Change"];

// Tickets.
export type Ticket = Schema["TicketListed"];
export type TicketDetail = Schema["Ticket"];
export type TicketComment = Schema["TicketComment"];
export type TicketKind = Ticket["kind"];
export type TicketPriority = Ticket["priority"];

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

// The agent registry. `/agents` lists each agent as its card on the Agents page shows it.
export type AgentSummary = Schema["AgentCard"];
export type AgentDetail = Schema["Agent"];
export type TiedRoadmap = Schema["TiedRoadmap"];
export type TiedRepository = Schema["TiedRepository"];
export type RegistryWork = Schema["AgentWork"];
export type RegistryRole = Schema["AgentRole"];
export type TieReason = Schema["TiedWorkItem"]["via"][number];
/** A tracked chat whose chat ID the agent reported. */
export type TiedChat = Schema["TiedChat"];
export type RoadmapTie = Schema["RoadmapTie"];
export type RoadmapWorker = Schema["RoadmapWorker"];
/** An agent whose work ties it to a roadmap, now or before, with the steps it touched. */
export type RosterAgent = Schema["RosterAgent"];
export type RoadmapAgents = Schema["RoadmapAgents"];
export type AgentActivity = Schema["AgentActivity"]["items"][number];

// Discussions.
export type DiscussionContextType = Schema["ContextTag"]["type"];
/** A thread's tag in one shape: what every kind has, and each kind's own fields where it has them. */
export type DiscussionContext = Schema["ContextTag"] &
  Partial<
    Omit<
      Schema["StepContextTag"] &
        Schema["DecisionContextTag"] &
        Schema["RepositoryContextTag"] &
        Schema["TicketContextTag"],
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

// Work, days and the records they hold.
/** A work item's status, which can also be waiting on someone else. */
export type ItemStatus = Schema["WorkItemRecord"]["status"];
/** A ticket's status, and the statuses a step's menu offers. */
export type Status = Schema["TicketListed"]["status"];
/** A work item as other records embed it. */
export type Item = Schema["WorkItemRecord"];
/** A work item as the work list shows it, with its stack rank. */
export type ItemListed = Schema["WorkItemListed"];
/** A work item's own page. */
export type Detail = Schema["WorkItem"];
export type RankResult = Schema["RankResult"];
export type Day = Schema["Day"];
/** A work item's entry on a day, with the item. */
export type Entry = Schema["DayEntry"];
export type TabCounts = Schema["TabCounts"];
export type Note = Schema["Note"];
export type Attachment = Schema["Attachment"];
export type Reference = Schema["FileReference"];
export type WebLink = Schema["WebLink"];
/** A tracked agent chat on a work item or roadmap. */
export type AgentChat = Schema["TrackedChat"];
/** A GitHub reference as its owner lists it, with the title of the chat that made it. */
export type GithubRef = Schema["ListedGithubReference"];

// Roadmaps.
export type Roadmap = Schema["Roadmap"];
export type RoadmapListed = Schema["RoadmapListed"];
export type Step = Schema["RoadmapStep"];
/** A step's own fields, without its notes and files, as the structure editor drafts them. */
export type PlanStep = Schema["PlanStep"];
/** A work item on a roadmap's page, with the people tied to it. */
export type RoadmapWorkItem = Schema["RoadmapWorkItem"];
export type RoadmapRef = Schema["FamilyRoadmap"];
export type RoadmapQuestion = Schema["Question"];
export type RoadmapDecision = Schema["Decision"];

// People.
/** A person tied to a work item, roadmap or repository; relationship is their role there. */
export type PersonAssignment = Schema["RecordPerson"];
/** A person as the people list shows them, with counts of what they are tied to. */
export type Person = Schema["PersonListed"];
export type PersonDetail = Schema["Person"];
/** A person's own fields, which the list and their page both have. */
export type PersonRecord = Schema["PersonRecord"];
export type PersonRoadmapLink = Schema["PersonRoadmap"];
export type PersonWorkItemLink = Schema["PersonWorkItem"];
export type PersonRepositoryLink = Schema["PersonRepository"];

// Chats and transcripts.
export type ChatAgent = Schema["ChatAgent"];
export type ChatThread = Schema["TranscriptThread"];
/** A chat's transcript summary in one shape: `error` is there when it could not be read. */
export type TranscriptSummary = Schema["TranscriptSummary"] &
  Partial<Pick<Schema["UnreadTranscript"], "error">>;
export type ChatOverview = Omit<Schema["ChatListed"], "transcript"> & {
  transcript: TranscriptSummary;
};
export type RunningSession = Schema["ClaudeSession"];
/** A transcript message in one shape: what every kind has, and each kind's own fields where it has them. */
export type ChatMessage = Merged<Schema["Transcript"]["messages"][number]>;
export type Transcript = Omit<Schema["Transcript"], "messages"> & {
  messages: ChatMessage[];
};

// Proposals and Settings.
export type Operation = Schema["ProposalOperation"];
/** A proposal as the review list shows it. */
export type ProposalListed = Schema["ProposalListed"];
/** A proposal with its notes, as its own page reads it. */
export type Proposal = Schema["Proposal"];
/** A proposal as a history lists it; a work item's or roadmap's also names the changes made to it. */
export type ProposalEntry = Schema["ProposalEntry"] &
  Partial<Pick<Schema["AppliedProposal"], "changes">>;
export type Settings = Schema["Settings"];

// Usage.
/** Which pages and API calls were used in a window of days, and which nothing used. */
export type UsageReport = Schema["UsageReport"];
/** One day's page views, reads and changes by the person and by agents, and failed calls. */
export type UsageDay = Schema["UsageDay"];
