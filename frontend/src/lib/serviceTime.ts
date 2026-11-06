// SQLite can serialize the service's UTC timestamps without their offset.
const naiveServiceTime = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/;

export function serviceTimestamp(value: string): number {
  return Date.parse(naiveServiceTime.test(value) ? `${value}Z` : value);
}
