// Failure reasons, in words the client can act on.
//
// `jobs.failure_reason` is written by the orchestrator as "<stage>: <CODE>" —
// "video: VENDOR_TIMEOUT". That string names an internal stage and an internal
// vendor, and this panel shows neither. It is also not useful: nobody outside
// the pipeline can do anything with VENDOR_TIMEOUT.
//
// So it is translated. Anything unrecognised becomes one generic line rather
// than falling through to the raw string — a fallback that leaks is a fallback
// that leaks exactly when something new breaks, which is the worst moment.

const BY_CODE: Record<string, string> = {
  VENDOR_TIMEOUT: "Video generation did not finish in time.",
  VENDOR_REJECTED: "The photo was rejected by automated content checks.",
  BUDGET_EXHAUSTED: "The daily processing limit was reached.",
  ASSET_MISSING: "A required file was missing.",
  FFMPEG_FAILED: "The final video could not be assembled.",
  CHECK_FAILED: "The finished video did not pass quality checks.",
  INTERNAL: "Processing failed unexpectedly.",
};

const GENERIC = "Processing failed.";

export function failureText(reason: string | null | undefined): string {
  if (!reason) return GENERIC;
  // "<stage>: <CODE>" — take the code and drop the stage, which is ours.
  const code = reason.includes(":") ? reason.split(":").pop()!.trim() : reason.trim();
  return BY_CODE[code.toUpperCase()] ?? GENERIC;
}

// Intake rejections, which are about the submission rather than the render.
// These are the ones the client can genuinely fix, by going back to the mechanic.
const BY_REJECT: Record<string, string> = {
  NOT_APPROVED: "Submission was not approved.",
  FACE_COUNT_NOT_1: "The photo does not show exactly one face.",
  BAD_MIME: "The photo is not a supported image format.",
  IMAGE_TOO_SMALL: "The photo is too small to use.",
  BAD_PHONE: "The phone number is not a valid Indian mobile.",
  NAME_TOO_LONG: "The name is too long to fit on the card.",
  WORKSHOP_TOO_LONG: "The workshop name is too long to fit on the card.",
  BAD_ADDRESS: "The address is missing or too long.",
  GENDER_UNSUPPORTED: "That gender value is not supported.",
  UNKNOWN_BACKGROUND: "The background choice was not recognised.",
  UNKNOWN_OUTFIT: "The uniform choice was not recognised.",
  TEST_ROW: "Marked as a test submission.",
  FETCH_FAILED: "The photo could not be downloaded.",
  MISSING_FIELD: "A required field was empty.",
};

export function rejectText(code: string | null | undefined): string {
  if (!code) return "Not accepted.";
  return BY_REJECT[code.toUpperCase()] ?? "Not accepted.";
}
