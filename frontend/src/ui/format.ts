export function formatBytes(value: number) {
  return value < 1024
    ? `${value} B`
    : value < 1048576
      ? `${(value / 1024).toFixed(1)} KB`
      : `${(value / 1048576).toFixed(1)} MB`;
}

/** A day such as "Nov 20", from its ISO date, with the year when it is not this one. */
export function formatDay(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, month - 1, day).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    ...(year === new Date().getFullYear() ? {} : { year: "numeric" }),
  });
}

const compact = new Intl.NumberFormat(undefined, {
  notation: "compact",
  maximumFractionDigits: 1,
});

/** A count in full under 10,000, such as "1,284", and shortened from there, such as "12.9K". */
export function formatCount(value: number) {
  return value < 10_000 ? value.toLocaleString() : compact.format(value);
}
