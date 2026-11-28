import type { components } from "./generatedApi";

type Schema = components["schemas"];

export type DeliverableStatus = Schema["DeliverableSummary"]["status"];
export type DeliverableReference = Schema["DeliverableReference"];
export type DeliverableReferenceInput =
  | Schema["DeliverableUrl"]
  | Schema["DeliverableRecordReference"]
  | Schema["DeliverableProposalAttachment"];
export type DeliverableRequirement = Schema["DeliverableRequirement"];
export type DeliverableRequirementInput = Schema["DeliverableRequirementInput"];
export type DeliverableSummary = Schema["DeliverableSummary"];
export type DeliverableDetail = Schema["DeliverableDetail"];
export type DeliverableCounts = Schema["DeliverableCounts"];
export type DeliverablesAnswer = Schema["DeliverableList"];
export type DeliverableStep = Schema["DeliverableStep"];
export type DeliverableOption = Schema["DeliverableOption"];
export type DeliverableOptionsAnswer = Schema["DeliverableOptions"];
export type DeliverableBacklinks = Schema["DeliverableBacklinks"];
export type DeliverableHistoryItem = Schema["DeliverableAcceptance"];
export type DeliverableHistoryEvent = Schema["DeliverableEvent"];
export type DeliverableHistoryAnswer = Schema["DeliverableHistory"];
export type DeliverableCreateBody = Schema["DeliverableCreate"];
/** The API accepts sparse PATCHes even though several create-like fields have defaults. */
export type DeliverableUpdateBody = {
  revision: number;
} & Partial<Omit<Schema["DeliverableUpdate"], "revision">>;
export type DeliverableAcceptBody = Schema["DeliverableAccept"];
export type DeliverableReopenBody = Schema["DeliverableReopen"];
export type DeliverableExpectedBody = Schema["Expected"];
