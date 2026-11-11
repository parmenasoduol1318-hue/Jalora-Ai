const FALLBACK_TIME_ZONE = "UTC";

export function resolveTimeZone(timeZone?: string | null): string {
  if (timeZone) {
    try {
      new Intl.DateTimeFormat(undefined, { timeZone });
      return timeZone;
    } catch (error) {
      if (!(error instanceof RangeError)) throw error;
    }
  }

  return Intl.DateTimeFormat().resolvedOptions().timeZone || FALLBACK_TIME_ZONE;
}
